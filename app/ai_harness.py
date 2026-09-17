"""DeepSeek Harness 接入层 —— 把 dsh 当成「带工具的问答引擎」来用。

## 为什么需要这一层

第七版把 AI 内核从手写循环换成 dsh(见 `doc/第七版*.md`)。dsh 的 Python SDK 是
**子进程驱动**:`DeepSeekHarness(...).start()` 起一个 node 进程,`run()` 通过 stdio
上的 JSON-RPC 跑一轮。这个模块管三件 dsh 特有的事,好让 `ai_agent.py` 保持干净:

  1. **profile patch** —— 每个提问者一份,决定「给他挂哪些工具、用什么系统提示」。
  2. **实例池** —— 起一个实例约 0.6 秒,不能每问一次起一个。
  3. **事件翻译** —— dsh 的通知 → 我们前端认的那 7 种事件。

## 安全:三件在 patch 里写死的事

**这三条是承重墙,不是配置偏好**(见 `ai_tools.py` 模块头):

  - `persistent-bash` / `persistent-pwsh` **`disabled: true`** —— dsh 的
    `sdk-minimal` 默认带持久化 shell 且 pin 了 `danger-full-access`,即「容器内
    任意代码执行」。容器里躺着 `.env`(DeepSeek key)、数据库连接串和全部代码。
    禁掉之后模型手上**只剩我们那 10 个只读查询工具**(真机验过:它会说
    「我没有可以执行 shell 命令的工具」)。
  - `session-log-deepseek` **`disabled: true`** —— 这个插件默认把完整会话日志
    上传给 DeepSeek。我们库里是产品档案、需求、博客正文,不该出境。
  - **不引入任何第三方 MCP 工具服务** —— 只挂 `app/ai_mcp_server.py`,
    那 10 个工具全是只读查询。

## 身份:一个提问者一个实例

工具要按提问者的权限裁剪,而工具服务是**独立进程**,拿不到 FastAPI 请求里的
`Viewer`。所以身份走 patch 里的**环境变量**,由 dsh 在拉起工具服务时注入 ——
一个 viewer 一个 harness 实例,每个实例的工具子进程带着各自的身份快照。

**为什么不是「一个会话一个实例」**:真机验过,一个实例可以服务多个会话
(`run(session_id=...)`),会话之间隔离正确。按 viewer 缓存,进程数随
**活跃用户数**增长,而不是随会话数 —— 后者在多人使用时会长得很快。

## 历史由我们自己回放,dsh 的会话是无状态的

每次提问用**独立的 session_id**,历史由 `ai_agent._history()` 从 Postgres
还原后拼进输入。理由:

  - Railway 的磁盘是**易失**的,重启/发版后 `$DSH_HOME/sessions` 里的 JSONL 就没了。
    若依赖它记上下文,会出现「界面上历史还在,模型却忘了」的不一致。
  - 多副本部署时,同一个会话的下一次提问可能落到另一个副本上,那边没有它的会话。
  - v6 本来就是每轮重发完整历史,所以**token 花销与现在持平**,不是新增代价。

代价也要认:dsh 自己的会话压缩、历史管理这些能力我们用不上。
"""
from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator

from . import ai_tools
from .ai_tools import Viewer

log = logging.getLogger("producthub.ai")

# dsh 在 profile 里把工具名拼成 `mcp__<serverName>__<rawName>`。前端只该看到裸名。
_MCP_PREFIX = "mcp__producthub__"

# 实例空闲多久回收。dsh 实例 = 1 个 node 进程 + 1 个工具服务进程,不能只进不出。
_IDLE_TTL_SECONDS = float(os.getenv("AI_HARNESS_IDLE_TTL", "600"))

# 起停超时。启动慢过这个值说明环境有问题(真机实测 0.5–0.6 秒)。
_START_TIMEOUT = float(os.getenv("AI_HARNESS_START_TIMEOUT", "30"))

# 给前端「工具轨迹」看的一小段。与 ai_agent 里那几个常量同一个值 ——
# 完整结果**不进前端**(那是模型上下文,一条可达 3 万字)。
_TRACE_PREVIEW_CHARS = 400
_TRACE_REASONING_CHARS = 1500


def _dsh_home() -> Path:
    """dsh 的工作目录(会话 JSONL、profile、node_modules 都在这)。

    必须是**可写**目录。默认放项目根的 `.dsh/`,已在 .gitignore 里。
    """
    return Path(os.getenv("AI_DSH_HOME", Path(__file__).resolve().parent.parent / ".dsh"))


def _workspace() -> Path:
    """给 dsh 的 cwd。**必须存在**。里面刻意什么都不放。

    模型没有文件类工具(见模块头的安全说明),所以这个目录它碰不到;
    存在的意义只是满足运行时的 cwd 要求。**不要指向项目根** ——
    万一将来有人误开了某个文件工具,爆炸半径会从「一个空目录」变成「整个仓库」。
    """
    path = Path(os.getenv("AI_DSH_WORKSPACE", _dsh_home() / "workspace"))
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------- patch 生成

def _yaml_scalar(value: str) -> str:
    """把任意字符串写成**安全的 YAML 双引号标量**。

    YAML 的双引号标量接受 JSON 的转义序列,所以 `json.dumps` 的输出直接就是合法的
    YAML —— 不用为此引入 PyYAML(requirements 里没有,也不想为一个字符串加一个依赖)。

    这条路径上确实需要转义:提问者的**姓名是用户可控的**,里面出现引号、冒号、
    换行都不奇怪。手工拼字符串迟早会在某个姓名上炸掉,而且是那种「大部分用户没事」
    的 bug。
    """
    return json.dumps(value, ensure_ascii=False)


def _patch_text(viewer: Viewer, system_prompt: str) -> str:
    """生成这个提问者的 profile patch。

    结构对照(dsh 的 patch 是「按 id 定位的覆盖 / 禁用 / 插入列表」):

        - id: <已有的行>   + disabled: true     → 关掉它
        - id: <已有的行>   + config: {...}      → 覆盖它的配置
        - insert: [ {id, name, config} ]        → 插一行新的

    ⚠️ **下面每一处插值都必须过 `_yaml_scalar`,包括注释里那个姓名。**
    这不是格式洁癖:姓名是用户可控的,而注释也是**行**——一个带换行的姓名会从
    注释里"越狱",在承重墙**之前**插进真正的 patch 行。构造出来的效果是给别人
    起一个带第三方 MCP 工具服务的实例(即容器内任意命令)。名字可以改这件事
    由 `routers/users.py` 的 `require_admin` 兜着,但那道门只挡到"应用管理员",
    挡不住"应用管理员 → 容器内执行"这一步。
    见 `tests/test_ai_kernel.py::PatchEscapingTests`。
    """
    return f"""# 由 app/ai_harness.py 生成,**不要手改** —— 每次起实例时按 viewer 重写。
# 提问者: {_yaml_scalar(viewer.name)}(id={viewer.id}, role={viewer.role})

# 承重墙 1:会话日志默认上传给 DeepSeek,关掉。
- id: session-log-deepseek
  disabled: true

# 承重墙 2:bundled profile 带持久化 shell 且 pin 了 danger-full-access。
# 关掉它们,模型手上只剩下面那个只读工具服务。
- id: persistent-bash
  disabled: true
- id: persistent-pwsh
  disabled: true

# 用我们自己的系统提示(personaPrefix 不覆盖的话是 dsh 的默认人格)。
- id: system-prompt
  config:
    personaPrefix: {_yaml_scalar(system_prompt)}

# 我们自己的只读工具,走 MCP stdio。
- insert:
    - id: producthub-tools
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: producthub
        transport: stdio
        command: {_yaml_scalar(_python_executable())}
        args:
          - "-m"
          - "app.ai_mcp_server"
        cwd: {_yaml_scalar(str(Path(__file__).resolve().parent.parent))}
        env:
          PRODUCTHUB_VIEWER_ID: {_yaml_scalar(str(viewer.id))}
          PRODUCTHUB_VIEWER_ROLE: {_yaml_scalar(viewer.role)}
          PRODUCTHUB_VIEWER_NAME: {_yaml_scalar(viewer.name)}
        # 起不来就报错,不要静默降级成一个没有工具的实例 ——
        # 「模型查不到东西」和「工具没挂上」在日志里必须能分辨。
        failOnStartupError: true
"""


def _python_executable() -> str:
    """用**当前解释器的绝对路径**拉起工具服务。

    不写 `python` —— 生产容器里 PATH 可能没有它,而 `sys.executable` 一定指向
    正在跑的这个环境(里面装着 sqlalchemy / psycopg)。
    """
    import sys

    return sys.executable


# ---------------------------------------------------------------- 实例池

@dataclass
class _Entry:
    harness: Any
    lock: threading.Lock          # 一个实例同时只跑一轮(见 acquire 的说明)
    last_used: float
    patch_path: Path


_pool: dict[str, _Entry] = {}
_pool_lock = threading.Lock()
_patch_dir: Path | None = None


def _viewer_key(
    viewer: Viewer,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
) -> str:
    """缓存键。**role 也要进键** —— 同一个人被改了角色之后,工具服务的身份快照
    必须跟着换,否则会拿旧角色继续查(那是权限泄漏,不是缓存陈旧)。

    **`max_tokens` 与 `reasoning_effort` 也要进键**,理由不同:它们是 `AgentOptions`
    的字段,只在建实例那一刻生效(见 `_start`)。不进键的话,改了之后池子里那个旧实例
    会继续用旧值服务最多 `_IDLE_TTL_SECONDS`(默认 10 分钟)——「我改了设置但没生效」
    是最难查的一类问题。进键之后,新值立刻起新实例,旧实例按空闲超时自己退场。
    """
    return ":".join([
        str(viewer.id), viewer.role,
        "-" if max_tokens is None else str(max_tokens),
        reasoning_effort or "-",
    ])


def _write_patch(viewer: Viewer, system_prompt: str) -> Path:
    """把 patch 写到磁盘,返回路径。

    patch 必须落到**文件**上:SDK 的 `patches=` 收的是路径,dsh 自己去读。
    用 `_patch_dir` 下的临时文件,进程退出时由 `shutdown_all()` 清掉。
    """
    global _patch_dir
    if _patch_dir is None:
        _patch_dir = _dsh_home() / "patches"
        _patch_dir.mkdir(parents=True, exist_ok=True)
    path = _patch_dir / f"viewer-{viewer.id}-{viewer.role}.yml"
    text = _patch_text(viewer, system_prompt)
    # 内容没变就不重写 —— dsh 那边 patch 变了会触发 reload,白白多花一次启动。
    if not path.exists() or path.read_text(encoding="utf-8") != text:
        path.write_text(text, encoding="utf-8")
    return path


def _sweep_sessions() -> None:
    """删掉过期的 dsh 会话目录。

    每次提问都是一条**全新会话**(见 `run_question`),所以 `$DSH_HOME/sessions`
    下的目录只增不减 —— 不清的话磁盘会被慢慢吃满,而里面存的是被我们刻意
    放弃的历史(我们自己从 Postgres 重放)。按修改时间删,过期的就没用了。

    失败一律吞掉:清不动磁盘不是「回答不了这个问题」的理由。
    """
    # ⚠️ 这里必须自己取**墙钟**:池子里那个 `now` 是 `time.monotonic()`,
    # 拿它跟文件系统的 `st_mtime` 相减是没有意义的(两个不同的原点)。
    now = time.time()
    root = _dsh_home() / "sessions"
    if not root.is_dir():
        return
    for workspace_dir in root.iterdir():
        if not workspace_dir.is_dir():
            continue
        for session_dir in workspace_dir.iterdir():
            try:
                if now - session_dir.stat().st_mtime <= _IDLE_TTL_SECONDS:
                    continue
                shutil.rmtree(session_dir, ignore_errors=True)
            except OSError:
                continue


def _sweep_idle_locked(now: float) -> None:
    """回收空闲实例。调用方必须已持有 `_pool_lock`。

    惰性回收(在 acquire 时顺手做)而不是起一个后台线程:后台线程要处理
    「解释器正在关闭」的一堆边界,而这里的回收本来就不要求及时。
    """
    for key, entry in list(_pool.items()):
        if now - entry.last_used <= _IDLE_TTL_SECONDS:
            continue
        log.info("回收空闲的 dsh 实例 %s", key)
        try:
            entry.harness.close()
        except Exception:  # noqa: BLE001 —— 回收失败不该影响正在等的这次提问
            log.exception("关闭 dsh 实例 %s 时出错", key)
        _pool.pop(key, None)


def acquire(
    viewer: Viewer,
    system_prompt: str,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
):
    """取这个提问者的实例,返回 `(harness, lock)`。

    **一个实例同时只跑一轮**(调用方要持有返回的 lock):SDK 的 JSON-RPC 客户端
    是否支持并发会话没有验证过,而「没验证过就并发」在这里的代价是串话或崩溃。
    串行化的代价是同一个人的两个会话要排队 —— 这个代价是我们主动选的。
    """
    key = _viewer_key(viewer, max_tokens, reasoning_effort)
    now = time.monotonic()
    _sweep_sessions()
    with _pool_lock:
        _sweep_idle_locked(now)
        entry = _pool.get(key)
        if entry is not None:
            entry.last_used = now
            return entry.harness, entry.lock

    # 起实例要 0.6 秒,**不要握着 _pool_lock 做** —— 那会让其他 viewer 全部排队。
    patch_path = _write_patch(viewer, system_prompt)
    harness = _start(patch_path, max_tokens, reasoning_effort)

    with _pool_lock:
        # 竞态:两个线程可能同时为同一个 key 起实例,谁先放进池子谁赢,
        # 后到的把自己起的那个关掉。
        existing = _pool.get(key)
        if existing is not None:
            existing.last_used = time.monotonic()
            try:
                harness.close()
            except Exception:  # noqa: BLE001
                log.exception("关闭多余的 dsh 实例时出错")
            return existing.harness, existing.lock
        entry = _Entry(
            harness=harness, lock=threading.Lock(),
            last_used=time.monotonic(), patch_path=patch_path,
        )
        _pool[key] = entry
        log.info("起了 dsh 实例 %s(patch=%s)", key, patch_path.name)
        return entry.harness, entry.lock


# ---------------------------------------------------------------- 配置查询

def is_ai_configured() -> bool:
    """有没有 key。**函数内读 env** —— 测试靠中途改环境变量来切换这个状态。"""
    return bool(os.getenv("DEEPSEEK_API_KEY"))


def model_name() -> str:
    """**模型名绝不硬编码。**

    DeepSeek 已经改过一轮命名:`deepseek-v4-flash` 现在是**退役别名**(仍被接受,
    但背后服务的是另一个模型),现行是 `deepseek-flash` / `deepseek-v4-pro`。
    写死一个常量等于给自己埋一次「我们没改代码但线上挂了」。

    ⚠️ 这个函数**同时是** `/api/ai/status` 报给管理员看的名字**和**
    `_start` 真正启动时用的名字 —— 两处各写一份默认值就会撒谎(第七版之前
    这里和 ai_client 的默认值就是不一致的:`deepseek-flash` 对 `deepseek-v4-flash`)。
    """
    return os.getenv("AI_MODEL", "deepseek-flash")


def _reasoning_effort() -> str | None:
    """思考档位,从 `AI_REASONING_EFFORT` 读。

    合法值 `off` / `low` / `high` / `max`。**不校验** —— 理由见 `_start`。

    **不给的效果就是 `high`,但别写成「不给就是 dsh 的默认 high」**(这版注释一度
    是这么写的,2026-09-17 核实后改掉)。机制是:不给 = 我们**根本不发** `reasoningEffort`
    这个字段,dsh 那侧省略就只是省略。真正定默认值的是**服务端** —— DeepSeek 官方
    文档(guides/thinking_mode)原话:「Thinking mode is enabled by default, with the
    default effort being `high`」。结论一样,理由不同:写错理由的人会以为 dsh 会替我们
    兜底一个档位,于是去 dsh 里找那个默认值,找不到。

    (顺带记一下官方那张映射表:`minimal`→low、`medium`→high、`xhigh`→high、`max`/`ultra`→max。
    公开的控制值只有 low / high / max,所以中间那几个写进来等于没写。)

    ⚠️ 第七版之前这是 `ai_client` 逐次下发的一个请求字段;内核换了之后它变成
    `AgentOptions` 的字段(实例级),所以它和 `max_tokens` 一样进了实例池的键 ——
    否则改了这个变量要等最多 10 分钟才生效,而且**旧实例还在按旧档位烧钱**。
    """
    return os.getenv("AI_REASONING_EFFORT") or None


def _start(
    patch_path: Path,
    max_tokens: int | None = None,
    reasoning_effort: str | None = None,
):
    """起一个 dsh 实例。

    ⚠️ **延迟 import**:`deepseek_harness` 是可选的运行时依赖 —— 没装它的时候
    整个应用(包括不碰 AI 的那些页面)都该能起来。所以不在模块顶层 import,
    等到真的要起实例才 import,并给出能看懂的报错。
    """
    try:
        from deepseek_harness import DeepSeekHarness
    except ImportError as exc:  # pragma: no cover —— 环境问题,不是逻辑分支
        raise RuntimeError(
            "没有装 deepseek-harness-sdk。AI 功能需要它:"
            "pip install deepseek-harness-sdk"
        ) from exc

    harness = DeepSeekHarness(
        provider="deepseek-official",
        model=model_name(),
        # 思考档位。**不在这里校验**:与 `AI_MODEL` 一致 —— 值由部署者负责,
        # 而 dsh 会在 initialize 时明确拒绝非法值(合法的是 `off` / `low` /
        # `high` / `max`;不给的效果是 `high`,但那是**服务端**的默认,见
        # `_reasoning_effort`)。自己再维护一份白名单,等于给以后
        # 新增的档位埋一颗「我们悄悄忽略了它」的雷。
        reasoning_effort=reasoning_effort,
        cwd=str(_workspace()),
        dsh_home=str(_dsh_home()),
        profile=os.getenv("DSH_PROFILE", "sdk-minimal"),
        patches=(str(patch_path),),
        api_key=os.getenv("DEEPSEEK_API_KEY") or None,
        # 上游地址要能改:测试把它指到一个死地址,免得拿着假 key 去打**真实**端点
        # (v6 的测试就是这么钉住网络的,见 tests/test_ai.py;内核换了,这条不能丢)。
        base_url=os.getenv("AI_BASE_URL") or None,
        # 单次回答的输出上限。**必须在这里给,不能等 run() 时给** ——
        # dsh 的 maxTokens 是 `AgentOptions` 的字段,只存在于「建 agent」这一步
        # (`DeepSeekHarnessConfig.max_tokens` → `initialize()` → 请求头),
        # 每一轮复用同一个 agent,所以它是**实例级**的。
        # 见 tests/test_ai_kernel.py::MaxTokensTests。
        max_tokens=max_tokens,
        initialize_timeout_seconds=_START_TIMEOUT,
    )
    harness.start()
    return harness


def shutdown_all() -> None:
    """关掉所有实例。应用退出时调用(见 main.py 的 shutdown 钩子)。"""
    with _pool_lock:
        entries = list(_pool.values())
        _pool.clear()
    for entry in entries:
        try:
            entry.harness.close()
        except Exception:  # noqa: BLE001
            log.exception("关闭 dsh 实例时出错")


# ---------------------------------------------------------------- 事件翻译

def _tool_name(raw: str) -> str:
    """`mcp__producthub__search_posts` → `search_posts`(前端只认裸名)。"""
    return raw[len(_MCP_PREFIX):] if raw.startswith(_MCP_PREFIX) else raw


def _parse_args(raw: Any) -> dict:
    """工具的 arguments 是 **JSON 字符串**(真机确认),不是对象。"""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": raw}
        return parsed if isinstance(parsed, dict) else {"_args": parsed}
    return {}


def _result_of(data: dict) -> tuple[bool, str, str]:
    """从 `tool/result` 的嵌套结构里取 (成败, 正文, toolCallId)。

    真机结构(三层):
        data.message.content[0] = {type:"tool-result", toolCallId, content:[{type:"text",text}], isError}

    ⚠️ **结果里没有工具名**。`data.message.source` 只有 `{kind:"tool", callId}` ——
    所以名字必须靠 toolCallId 回到前面的 `tool/call` 去认(见 `_translate` 的 names)。
    之前我从 `source.name` 取,拿到的永远是空串,轨迹会显示成「某个工具」。
    """
    message = data.get("message") or {}
    for block in message.get("content") or []:
        if not isinstance(block, dict) or block.get("type") != "tool-result":
            continue
        is_error = bool(block.get("isError"))
        parts = [
            b.get("text", "")
            for b in block.get("content") or []
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        call_id = block.get("toolCallId") or (message.get("source") or {}).get("callId") or ""
        return is_error, "\n".join(parts), call_id
    return False, "", ""


def _preview(result: str) -> tuple[bool, str]:
    """从工具结果里取出「成功与否 + 给前端看的一小段」。

    与 `ai_agent._trace_preview` 是同一条规则,这里重写一遍而不是互相 import ——
    `ai_agent` 要 import 本模块,反向 import 会成环。

    成败**靠重新解析那段 JSON**判断,不是搜 `"error"` 子串:产品简介里出现
    「error」这个词是完全可能的。而且 `ai_tools.run_tool` 把**所有**失败都收敛成
    信封(它从不抛),所以 MCP 的 `isError` 永远是 false —— 真正的判据在这里。
    """
    body = result or ""
    if body.startswith("<tool_result"):
        body = body.split(">", 1)[-1]
    if body.endswith(ai_tools.RESULT_CLOSE):
        body = body[: -len(ai_tools.RESULT_CLOSE)]
    body = body.strip()

    ok = True
    try:
        payload = json.loads(body)
        ok = not (isinstance(payload, dict) and "error" in payload)
    except json.JSONDecodeError:
        pass  # 解析不了就当作成功,预览照给 —— 这只是个展示用的标记
    return ok, body[:_TRACE_PREVIEW_CHARS]


def _split_message(data: dict) -> tuple[str, str, dict]:
    """`assistant/message` → (思考, 正文, usage)。

    ⚠️ **整段到达,不是逐字**(这就是换内核的代价):dsh 内部有 text-delta,
    但被 BlockAssembler 折成了完整的块才发出来。所以 `content_delta` 事件在
    前端看来是「一大段」而不是「一个字」—— 事件形状不变,粒度变粗。
    """
    message = data.get("message") or {}
    reasoning: list[str] = []
    text: list[str] = []
    for block in message.get("content") or []:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind == "reasoning":
            reasoning.append(block.get("text") or "")
        elif kind == "text":
            text.append(block.get("text") or "")
        # `tool-call` 块忽略 —— 它由 tool/call 事件单独送达,重复发会让轨迹出现两遍
    return "".join(reasoning), "".join(text), (data.get("usage") or {})


def _add_usage(outcome: TurnOutcome, usage: dict) -> None:
    """把一次模型调用的 usage 累加进这一轮。

    ## 口径:prompt 是两个字段**相加**

        prompt_tokens     ← inputTokens + cacheReadTokens + cacheWriteTokens
        completion_tokens ← outputTokens
        (reasoningTokens 不加 —— 它已含在 output 里)

    **⚠️ 这是改过的,旧口径是错的,别再改回去。**

    旧版写的是 `prompt ← inputTokens`,理由抄在这里:「缓存命中的 token 已经含在
    input 里,加上去就是重复计费」。那条理由是**反推出来的** —— 我读的是 dsh 插件层
    `formatCacheHitPercent` 里 `promptTokens - cacheReadTokens` 这一行,看它敢做减法,
    就认定「能减 ⇒ 含在里面」。**看着严丝合缝,但是错的。**

    真机上一量,反例立刻出来 —— 七条样本(`/tmp/dsh_usage_probe.py`)**全部**满足:

        totalTokens = inputTokens + outputTokens + cacheReadTokens

    `cacheReadTokens` 是个**独立的加数**,`inputTokens` 只是没命中缓存的那部分
    (真机上 173–894,而 cacheRead 是 1024–2816 —— 大头在缓存这边)。
    按旧口径记账,每一轮 prompt 都被少记 1024–2816 个 token,真值是记下来的
    **4–7 倍**。少算钱没有任何外部症状,不主动量就永远发现不了。

    改成相加之后 `total = prompt + completion` 也就成立了,顺带说明它和 v6 是
    **同一个**口径:v6 直接读上游的 `prompt_tokens`,那是含缓存的整段 prompt。
    所以历史行不用迁移、可以直接相加 —— 反倒是旧口径才是那个跟 v6 对不上的。

    `cacheWriteTokens` 在这七条样本里**从来没出现过非零值**,所以「它是加数」和
    「它已含在 input 里」两种可能都解释得通。选了加数,因为这样才和上面那条等式
    自洽 —— 这个选择是**没有证据支撑的**,等式一旦被打破下面的探针会喊。

    ## 为什么还要断言

    dsh 是 `0.1.5rc1`(预发布),`totalTokens` 是可选的。上面那条等式是我们能拿到的
    最直接的探针,不一致就**大声记日志**(不改数 —— 不知道该往哪边改)。
    见 `tests/test_ai_kernel.py::UsageTests`。
    """
    prompt = (
        (usage.get("inputTokens") or 0)
        + (usage.get("cacheReadTokens") or 0)
        + (usage.get("cacheWriteTokens") or 0)
    )
    completion = usage.get("outputTokens") or 0
    outcome.prompt_tokens += prompt
    outcome.completion_tokens += completion

    total = usage.get("totalTokens")
    if total is not None and total != prompt + completion:
        log.warning(
            "dsh 的 usage 口径又变了:totalTokens=%s 但 input+cacheRead+cacheWrite"
            "+output=%s(input=%s cache_read=%s cache_write=%s output=%s "
            "reasoning=%s)。我们按这个和记账(见 _add_usage 的说明),"
            "这个等式一旦不成立,账本可能算错。",
            total, prompt + completion,
            usage.get("inputTokens"), usage.get("cacheReadTokens"),
            usage.get("cacheWriteTokens"), usage.get("outputTokens"),
            usage.get("reasoningTokens"),
        )


# ---------------------------------------------------------------- 跑一轮

@dataclass
class TurnOutcome:
    """一轮的结果。由 `run_question` 最后以 `_outcome` 事件交出。"""

    content: str = ""
    reasoning: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    trace: list[dict] = field(default_factory=list)
    title: str | None = None
    error: str | None = None
    finish_reason: str | None = None
    truncated: bool = False


def run_question(
    viewer: Viewer,
    *,
    prompt: str,
    session_id: str,
    system_prompt: str,
    budget_seconds: float | None = None,
    max_tokens: int | None = None,
) -> Iterator[dict]:
    """跑一轮,逐个 yield **我们前端认的事件**(形状见 `ai_agent.py` 模块头)。

    最后一个事件是 `{"type": "_outcome", ...}`,**不要转发给前端** ——
    `ai_agent.stream_answer` 消费它,把落库需要的东西取出来。

    为什么要开线程:`harness.run()` 是**阻塞**的,而且它的 `on_notification`
    回调是在同一个线程里同步触发的。想在 `run()` 还没返回时就把事件交给 SSE,
    就只能把它放到工作线程里,用队列把通知传出来。
    """
    harness, lock = acquire(
        viewer, system_prompt, max_tokens, _reasoning_effort()
    )
    # ⚠️ **会话 id 必须是每次全新的**,不能从 conversation_id / message_id 推导。
    # dsh 把会话持久化在 `$DSH_HOME/sessions` 下的 JSONL 里,**拒绝复用已存在的
    # session id**(报 `session "…" already exists`)。撞上的场景很实际:同一次
    # 提问重试、或者磁盘持久时前端重发。加了 uuid 之后,即便 (会话, 消息) 相同
    # 也是两条独立会话 —— 这正是我们要的,因为历史由我们自己重放(见模块头)。
    run_session_id = f"{session_id}-{uuid.uuid4().hex[:12]}"
    outcome = TurnOutcome()
    events: queue.Queue = queue.Queue()
    # 工作线程把结果放这儿,由消费者取用 —— 不能让工作线程直接写 outcome(见 worker)。
    result_box: dict = {}
    _DONE = object()

    def on_notification(notification) -> None:
        events.put(("n", notification))

    def worker() -> None:
        try:
            result = harness.run(
                prompt, session_id=run_session_id, on_notification=on_notification
            )
            # ⚠️ **这个线程绝对不许碰 `outcome.content`。**
            # 它比消费者快得多:`run()` 一返回,队列里多半还压着一堆通知没被翻译,
            # 此刻 `outcome.content` 还是空串 —— 在这里做「空了就兜底」的检查,
            # 会把 `final_response`(完整正文)抢先写进去;接着消费者翻译到
            # `outcome.content += text`,正文就成了**两份**。
            # 第七版线上真的这样重复过:界面上整段回答出现两遍,而且因为历史会被
            # 重放,下一轮模型还能看见自己那份重复的回答、并在回答里评论它。
            # 兜底逻辑因此挪到了循环之后、消费者线程里(见下面的 `if not outcome.content`)。
            # 这里只把结果放进 `result_box` 交给它。
            result_box["final_response"] = result.final_response or ""
            outcome.finish_reason = result.finish_reason
        except Exception as exc:  # noqa: BLE001 —— 收敛成 error 事件交给上层
            log.exception("dsh 跑一轮时出错")
            outcome.error = f"{type(exc).__name__}: {exc}"
        finally:
            events.put((_DONE, None))

    # 同一个实例上的提问**串行**:拿不到锁就在这儿等。这是主动选的代价,
    # 换来的是不必去赌 SDK 的并发安全性(见 acquire 的说明)。
    #
    # ⚠️ **超时那条路上这条保证会漏。** 到点我们从 `with lock` 里退出去,而上面那个
    # 线程还在跑 —— 下一个人拿到同一把(已释放的)锁,就会和它**同时**在跑。
    # 读 SDK 源码看,这件事应该是安全的:`Session.run` 给每次运行建**自己的**订阅
    # (`subscribe_session_notifications(self.id)`),收到的通知再按 `sessionId` 过滤,
    # 而我们的 session id 每次都是新的 uuid。也就是说并发两次运行 = dsh 上两个各走
    # 各的会话,这正是它作为一个多会话服务本来就支持的形状。
    # 但这只是**读出来的**,没有压过。真出问题时的症状是两个人的回答串在一起。
    with lock:
        thread = threading.Thread(target=worker, name="dsh-run", daemon=True)
        thread.start()
        deadline = (
            time.monotonic() + budget_seconds if budget_seconds else None
        )

        # toolCallId → 轨迹里的那一条。`tool/result` 既不带头具名也不带参数,
        # 只能靠 id 回到 `tool/call` 建的那条上去**回填** ok/preview。
        call_slots: dict[str, dict] = {}
        while True:
            # 墙钟预算:到点就不再往下消费,把已有的内容当作回答交出去。
            # ⚠️ **这只停掉「消费」,停不掉 dsh 那一轮本身。** 所以对外的上界是
            # 「预算 + 下面那句 join 的 1 秒」,而**花掉的钱没有上界**。
            # 已核实的两个事实(dsh 0.1.5rc1 的 Python SDK):
            #   - **没有取消接口**:`deepseek_harness` 里没有 cancel/abort/interrupt
            #     任何一个词(插件层有 `agent.cancel()`,SDK 没把它暴露出来)。
            #   - **也没有步数上限**:`dsh-agent-loop` 的配置表只有
            #     `maxParallelToolCalls` 和 `agents[]` 那几个字段,没有 maxSteps 之类。
            #     所以一轮何时结束只由模型决定(不再要工具就结束)。
            # 与 v6 的差别要认:v6 能真的把上游连接关掉、当场止血;现在不能。
            if deadline and time.monotonic() >= deadline:
                outcome.truncated = True
                log.warning("提问 %s 到了墙钟预算,不再等待 dsh", session_id)
                break
            try:
                kind, payload = events.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind is _DONE:
                break
            for event in _translate(payload, outcome, call_slots):
                yield event

        thread.join(timeout=1.0)

    # 兜底:一条正文都没收到时才用 final_response(比如翻译层把带正文的通知丢了)。
    # ⚠️ **必须在这里、不能在 worker 里** —— 那里是一次竞态,会把正文写成两份,
    # 理由见 worker 的注释。到这一步消费者已经不再动 `outcome.content` 了。
    #
    # 正文以**累积的**为准,不是 final_response:模型经常在调工具前先说一句
    # 「我来查一下」,那条也是 content_delta、已经推给前端了;只存 final_response
    # 会让「看到的」和「存下来的」对不上。
    if not outcome.content:
        outcome.content = result_box.get("final_response") or ""

    yield {"type": "_outcome", "outcome": outcome}


def _round_for(outcome: TurnOutcome, step: Any) -> dict:
    """取(或新建)第 `step` 步的轨迹条目。

    `tool_trace` 的落库形状是 v6 定的、前端已经在渲染的:
        [{"round": n, "reasoning": "…", "calls": [{"name","args","ok","preview"}]}]
    dsh 的对应物是 **step**(一个 step = 一次模型调用 + 它发起的工具)。
    这里换的是分组依据,形状**保持不变** —— 前端一行都不用改。
    """
    for entry in outcome.trace:
        if entry["round"] == step:
            return entry
    entry = {"round": step, "reasoning": "", "calls": []}
    outcome.trace.append(entry)
    return entry


def _translate(
    notification, outcome: TurnOutcome, call_slots: dict[str, dict]
) -> Iterator[dict]:
    """一条 dsh 通知 → 零条或多条我们的前端事件。

    **认不出来的类型直接丢掉** —— `request/header` 这类内部节奏对前端没有意义,
    而把它们透出去等于把 dsh 的内部事件模型变成前端的契约,以后 dsh 改一次
    我们就要跟着改一次。
    """
    payload = getattr(notification, "payload", None) or {}
    event = payload.get("event") if isinstance(payload, dict) else None
    if not isinstance(event, dict):
        return

    kind = event.get("type")
    data = event.get("data") or {}

    if kind == "tool/call":
        name = _tool_name(data.get("name") or "")
        args = _parse_args(data.get("arguments"))
        slot = {"name": name, "args": args, "ok": None, "preview": ""}
        if data.get("callId"):
            call_slots[data["callId"]] = slot
        # 挂在当前 step 下。`tool/call` 事件自己带 step,不必依赖 step/start。
        _round_for(outcome, data.get("step", 0))["calls"].append(slot)
        yield {"type": "tool", "name": name, "args": args}

    elif kind == "tool/result":
        is_error, text, call_id = _result_of(data)
        ok, preview = _preview(text)
        slot = call_slots.get(call_id)
        if slot is not None:
            # 回填到那次调用上 —— 不在轨迹里另起一条,否则前端会看到
            # 「调用」和「结果」两行,而 v6 是一行。
            slot["ok"] = ok and not is_error
            slot["preview"] = preview
            name = slot["name"]
        else:
            name = ""
        yield {
            "type": "tool_result",
            "name": name,
            "ok": ok and not is_error,
            "preview": preview,
        }

    elif kind == "assistant/message":
        thought, text, usage = _split_message(data)
        if usage:
            _add_usage(outcome, usage)
        if thought:
            outcome.reasoning += thought
            entry = _round_for(outcome, data.get("step", 0))
            entry["reasoning"] = (entry["reasoning"] + thought)[:_TRACE_REASONING_CHARS]
            yield {"type": "reasoning_delta", "text": thought}
        if text:
            # ⚠️ 整段到达,不是逐字(这是换内核的代价)。模型在调工具前先说的话
            # 也会作为独立的一条 assistant/message 送来,所以这里**累积** ——
            # 让「推给前端的」和「落库的」是同一份(见 worker 里的说明)。
            outcome.content += text
            yield {"type": "content_delta", "text": text}
