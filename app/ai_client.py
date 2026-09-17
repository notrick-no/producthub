"""DeepSeek API 调用层(第六版)。

照 `app/mailer.py` 的先例:模块级异常 + `is_ai_configured()` + **函数内 `os.getenv`**
(`doc/架构.md:552` 的明文约定,测试要能中途覆盖 env),上层路由器把异常转成 503。

**用标准库 `urllib.request`,不引入 httpx** —— `requirements.txt` 只有 8 个包,
httpx 不是 fastapi/starlette 的硬依赖(只是 TestClient 的 extra)。本地 conda 环境里
有它,所以 `import httpx` 在你机器上不会报错,但**生产镜像里没有**。
`mailer.py` 用 urllib 调 Resend 已经证明这条路够用。

## 与 mailer 不同的四处(都是抄了会错的地方)

1. **超时是几十秒量级,不是 mailer 的 25 秒。** `urlopen(timeout=N)` 是**每个 socket
   操作**的超时。Resend 毫秒级返回;而大模型的**非流式**响应在生成完成前一个字节都不发,
   第一次 `recv()` 会阻塞整段生成时间 —— 25 秒必然超时。所以这里给 60 秒
   (默认值由 A0 探针的实测延迟确定,见 `_timeout_seconds`)。
2. **`except HTTPError` 必须排在 `except URLError` / `OSError` 前面。**
   `HTTPError` 是 `URLError` 的子类,`URLError` 又是 `OSError` 的子类。顺序反了,
   错误体永远读不到,只剩一句"网络错误"。mailer 那段顺序是承重的,这里同样。
3. **不做连接复用。** 一轮问答约 6 次调用 = 6 次 TCP+TLS 握手。这个开销相对几十秒的
   生成时间可以忽略 —— **不要**为了这个换成 httpx 或引入连接池。
4. **有重试,但只对「还没开始生成」的失败。** 见下面 `_open` 与 `stream` 的注释。

## 重试为什么敢做

**因为这一版的工具全部只读。** 重试一个会产生副作用的调用(写库、发信)可能造成重复,
重试一个查询不会。这个前提一旦不成立(将来加了可写的工具),**这里的重试必须重新审**。
"""
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request

log = logging.getLogger("producthub.ai")

DEFAULT_BASE_URL = "https://api.deepseek.com"

# 重试:只对「服务器忙 / 暂时性故障」。确定性错误(401 鉴权失败、402 余额不足、
# 400 请求体不合法)重试只会烧钱并拖长响应,一律直接抛。
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 3
_BACKOFF_SECONDS = (1.0, 3.0)  # 第一次失败后等 1s,第二次后等 3s

_ERROR_DETAIL_MAX = 500  # 与 mailer 一致:错误体只留前 500 字


class AiNotConfigured(Exception):
    """未配 `DEEPSEEK_API_KEY`(上层转 503)。"""

    def __init__(self) -> None:
        super().__init__("AI 未配置,无法使用:请在环境变量配置 DEEPSEEK_API_KEY")


class AiCallError(Exception):
    """调用上游失败:网络故障、鉴权失败、余额不足、请求被拒等(上层转 502/503)。"""

    def __init__(self, detail: str, *, status: int | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


# ---------------------------------------------------------------- 配置

def is_ai_configured() -> bool:
    """有没有 key。**函数内读 env** —— 测试靠中途改环境变量来切换这个状态。"""
    return bool(os.getenv("DEEPSEEK_API_KEY"))


def model_name() -> str:
    """**模型名绝不硬编码。**

    DeepSeek 已经改过一轮命名:`deepseek-v4-flash` 现在是**退役别名**(仍被接受,
    但背后服务的是另一个模型),现行是 `deepseek-flash` / `deepseek-v4-pro`。
    写死一个常量等于给自己埋一次「我们没改代码但线上挂了」。
    """
    return os.getenv("AI_MODEL", "deepseek-flash")


def _base_url() -> str:
    return os.getenv("AI_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def _timeout_seconds() -> int:
    """整次 HTTP 调用的 socket 超时。默认 60s,可用 AI_TIMEOUT 覆盖。

    ⚠️ 这是**每个 socket 操作**的超时,不是整次问答的总时长 —— 总时长由
    `ai_agent._wall_clock_budget()` 管。

    60 秒同样是 A0 探针实测后定的:流式请求的**首块**在 1.1 秒左右就到了(连
    194 个 reasoning token 那次也是),所以这个值真正的作用是「检测连接卡死」,
    而不是「等生成完」—— 生成再慢也在持续吐字节,碰不到它。调小它换来的是
    卡死时早 2 分钟结束、少花 2 分钟的钱。
    """
    try:
        return int(os.getenv("AI_TIMEOUT", "60"))
    except ValueError:
        return 60


def _max_tokens() -> int | None:
    """单次回复上限的**兜底值**;未设则不发送该字段(由服务端定默认)。

    ⚠️ 正常情况下这个值由**管理员在界面上设置**(`ai_settings.max_tokens_per_call`),
    经 `stream(max_tokens=…)` 显式传进来,这里只是它没传时的退路。
    两个出处的优先级是:**参数 > `AI_MAX_TOKENS` 环境变量 > 不发送**。
    留环境变量不是让人去线上配 —— 是给 spike 脚本和测试用的。
    """
    raw = os.getenv("AI_MAX_TOKENS")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


# ---------------------------------------------------------------- 请求构造

def _build_payload(
    messages: list,
    tools: list | None,
    *,
    stream: bool,
    max_tokens: int | None = None,
) -> dict:
    """组装 /chat/completions 的请求体。

    **这里刻意不发送 `temperature` / `presence_penalty` / `frequency_penalty`** ——
    思考模式下它们「设了不报错但完全无效」,写进来只是假装有用。
    (`top_p` 是另一回事:它生效,但下限被抬到 0.95。)

    思考模式**默认开启、effort 默认 `high`**。默认不发送任何思考相关字段 = 用服务端默认值;
    要调时设 `AI_REASONING_EFFORT`(`low` / `high` / `max`)。
    """
    payload: dict = {
        "model": model_name(),
        "messages": messages,
        "stream": stream,
    }
    if tools:
        payload["tools"] = tools
    limit = max_tokens if max_tokens is not None else _max_tokens()
    if limit is not None:
        payload["max_tokens"] = limit

    effort = os.getenv("AI_REASONING_EFFORT")
    if effort:
        payload["reasoning_effort"] = effort

    if stream:
        # 让最后一块带上 usage —— 不然流式下算不出花了多少钱。
        payload["stream_options"] = {"include_usage": True}
    return payload


def _error_detail(exc: urllib.error.HTTPError) -> str:
    """从错误体里挖出**上游自己的话**。

    「DeepSeek 说余额为零」和「上游返回 402」是五分钟与两小时的差别 ——
    前者直接告诉你该去充值,后者要你去翻文档。挖不出来就退回原始文本。
    """
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:  # noqa: BLE001 —— 读错误体失败不该盖住原始错误
        return f"HTTP {exc.code}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return f"HTTP {exc.code}: {raw[:_ERROR_DETAIL_MAX]}"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        message = err.get("message") or err.get("type") or json.dumps(err, ensure_ascii=False)
    elif err:
        message = str(err)
    else:
        message = raw
    return f"HTTP {exc.code}: {str(message)[:_ERROR_DETAIL_MAX]}"


def _open(payload: dict, timeout: int):
    """POST 并返回响应对象(调用方负责关闭)。**重试只发生在这里。**

    为什么重试只包住这一层:一旦响应开始吐字节,重试就会把**半截答案**和新的答案
    接在一起,产出一篇看起来完整、实际前后矛盾的回答。所以流开始之后的失败
    **不重试**,直接抛(见 `stream` 的注释)。
    """
    if not is_ai_configured():
        raise AiNotConfigured()

    url = f"{_base_url()}/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    last_error: str = "未知错误"
    for attempt in range(_MAX_ATTEMPTS):
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {os.getenv('DEEPSEEK_API_KEY', '')}",
                # 照 mailer 的先例给个正常 UA:某些前置会拦 Python-urllib 的默认 UA。
                "User-Agent": "producthub/3.0 (+https://github.com/notrick-no/producthub)",
            },
            method="POST",
        )
        try:
            return urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as exc:
            # ⚠️ 必须排在 URLError / OSError 之前 —— HTTPError 是它们的子类。
            last_error = _error_detail(exc)
            if exc.code not in _RETRY_STATUS:
                log.warning("AI 调用被拒(不重试): %s", last_error)
                raise AiCallError(last_error, status=exc.code) from exc
            log.warning("AI 调用失败将重试(%s/%s): %s", attempt + 1, _MAX_ATTEMPTS, last_error)
        except (urllib.error.URLError, OSError) as exc:
            last_error = f"网络错误: {exc}"
            log.warning("AI 调用网络失败将重试(%s/%s): %s", attempt + 1, _MAX_ATTEMPTS, exc)

        if attempt < len(_BACKOFF_SECONDS):
            time.sleep(_BACKOFF_SECONDS[attempt])

    raise AiCallError(f"重试 {_MAX_ATTEMPTS} 次后仍失败:{last_error}")


# ---------------------------------------------------------------- 非流式

def complete(
    messages: list,
    tools: list | None = None,
    *,
    max_tokens: int | None = None,
    timeout: int | None = None,
) -> dict:
    """一次非流式调用,返回上游的完整响应 dict。"""
    payload = _build_payload(messages, tools, stream=False, max_tokens=max_tokens)
    try:
        with _open(payload, timeout or _timeout_seconds()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except AiCallError:
        raise
    except (urllib.error.URLError, OSError) as exc:
        raise AiCallError(f"读取响应失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AiCallError("上游返回的不是合法 JSON") from exc


# ---------------------------------------------------------------- 流式

def stream(
    messages: list,
    tools: list | None = None,
    *,
    max_tokens: int | None = None,
    timeout: int | None = None,
):
    """流式调用,逐块 yield 上游的 chunk dict。

    **迭代响应对象取行,不要自己 read() 再按 `\\n\\n` 切。** `urlopen` 返回的是
    带缓冲的 IO 对象,`for line in resp` 会替我们把「一个 SSE 事件被拆成两次 TCP 读」
    这种情况处理好;手写切分则要自己维护半行缓冲,是这一层最容易写错的地方。

    SSE 的中间行(空行、`event:`、注释)一律跳过,只认 `data:` 开头的行;
    `data: [DONE]` 是结束哨兵。

    **流开始之后的失败不重试**(见 `_open` 的注释),直接抛 `AiCallError` ——
    调用方要把已经生成的部分保留下来,而不是丢弃。
    """
    payload = _build_payload(messages, tools, stream=True, max_tokens=max_tokens)
    resp = _open(payload, timeout or _timeout_seconds())
    try:
        for raw_line in resp:
            line = raw_line.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            body = line[len("data:") :].strip()
            if body == "[DONE]":
                return
            try:
                yield json.loads(body)
            except json.JSONDecodeError:
                log.warning("跳过无法解析的 SSE 数据行: %s", body[:200])
                continue
    except (urllib.error.URLError, OSError) as exc:
        # 中途断流:抛出去,让调用方决定怎么收尾(通常是保留已生成的内容 + 记 failed)。
        raise AiCallError(f"流式读取中断: {exc}") from exc
    finally:
        resp.close()


def usage_of(chunk: dict) -> dict | None:
    """从 chunk 里取 usage(只有流末尾那一块带,靠请求里的 stream_options 要来)。"""
    return chunk.get("usage")


def parse_error_body(status: int, body: str) -> str:
    """给测试与调试用:把一段错误体整理成一句话。"""
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return f"HTTP {status}: {body[:_ERROR_DETAIL_MAX]}"
    err = data.get("error") if isinstance(data, dict) else None
    if isinstance(err, dict):
        return f"HTTP {status}: {str(err.get('message') or err)[:_ERROR_DETAIL_MAX]}"
    return f"HTTP {status}: {str(err or data)[:_ERROR_DETAIL_MAX]}"


# 让测试能 patch 到 urlopen:`mock.patch.object(ai_client.urllib.request, "urlopen", fake)`。
# 前提是模块里 `import urllib.request` 而不是 `from urllib.request import urlopen` ——
# 后者会让 patch 无对象可指,测试会静默地打真网络。
_ = urlopen = urllib.request.urlopen  # noqa: E305  (仅为可读性保留一个短名字)
__all__ = [
    "AiCallError",
    "AiNotConfigured",
    "complete",
    "is_ai_configured",
    "model_name",
    "parse_error_body",
    "stream",
    "usage_of",
]
