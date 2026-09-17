"""AI 内核边界(第七版):profile 安全姿态、dsh 事件翻译、MCP 工具服务。

## 为什么单独一个文件

第七版把内核换成 DeepSeek Harness 之后,**「只读是承重墙」这句保证的落点变了**:

  - v6:靠 `ai_agent` 的循环里只调 `ai_tools` 那 10 个只读函数。
  - v7:靠 **profile patch 里那几行 `disabled: true`** —— 关掉 dsh 自带的
    持久化 shell(它 pin 了 `danger-full-access`,即容器内任意代码执行)。
    容器里躺着 `.env`(DeepSeek key)、数据库连接串和全部代码。

也就是说,**这堵墙现在是一份配置**。而配置是那种「谁顺手删一行,没有任何测试
会响、安全姿态就静默反转」的东西 —— 所以它必须被测试钉死,而且要在显眼的地方。

同样地 `session-log-deepseek`(默认把完整会话日志上传给 DeepSeek)也是配置。

## 这个文件不碰网络、不碰 dsh 子进程

全部是纯单元测试 + 本地测试库。真机联调是另一回事(见 `scripts/dsh_spike.py`),
这里钉的是**我们自己的代码和配置**。
"""
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from base import ApiTestCase  # noqa: F401 —— 必须最先导入,它设好测试库

from app import ai_harness, ai_mcp_server, ai_tools
from app.ai_agent import _compose_prompt
from app.ai_tools import Viewer

try:  # PyYAML 不在 requirements 里(应用不需要它)。有就用它加一道,
    import yaml as _yaml  # 没有也不影响下面的断言 —— 那些不依赖解析器。
except ImportError:  # pragma: no cover
    _yaml = None

try:  # SDK 是可选的运行时依赖(没装它整个应用也该起得来,见 ai_harness._start)
    import deepseek_harness
except ImportError:  # pragma: no cover
    deepseek_harness = None


# ---------------------------------------------------------------- 承重墙(配置)

class ProfilePatchSecurityTests(unittest.TestCase):
    """patch 的安全姿态。**删掉任何一条断言都等于放开了对应的口子。**"""

    def setUp(self) -> None:
        self.viewer = Viewer(id=42, role="employee", name="张三")
        self.text = ai_harness._patch_text(self.viewer, "系统提示")

    def test_local_shell_tools_are_disabled(self):
        """dsh 自带的持久化 shell 必须被关掉。

        它是 dsh `sdk-minimal` profile 的默认工具,且 pin 了 `danger-full-access`。
        不关的话,模型(以及任何能影响模型输出的人)可以在**生产容器里执行任意
        命令** —— 那个容器有 `.env`、数据库连接串和全部代码。
        """
        for row in ("persistent-bash", "persistent-pwsh"):
            self.assertIn(f"- id: {row}\n  disabled: true", self.text,
                          f"{row} 没被禁用 —— 容器内任意代码执行的口子开了")
            self.assertEqual(self.text.count(f"- id: {row}"), 1)

    def test_session_log_upload_is_disabled(self):
        """会话日志默认上传给 DeepSeek。库里是产品档案、需求、博客正文。"""
        self.assertIn("- id: session-log-deepseek\n  disabled: true", self.text)

    def test_only_our_own_tool_server_is_mounted(self):
        """只挂我们自己的只读工具服务,**不引任何第三方 MCP 服务**。

        第三方服务是「把别人的代码挂进我们的权限里」—— 一旦引入就绕过了
        `ai_tools` 那 10 个只读查询的边界,而且**不会体现在这个文件的任何
        其它断言上**,会是一个静默的口子。
        """
        self.assertIn("name: '@deepseek-ai/dsh-mcp-client'", self.text)
        self.assertEqual(self.text.count("name: '@deepseek-ai/dsh-mcp-client'"), 1,
                         "挂了不止一个 MCP 工具服务")
        self.assertIn("serverName: producthub", self.text)
        self.assertIn("app.ai_mcp_server", self.text)

    def test_tool_server_failure_is_loud(self):
        """起不来就报错,不静默降级。

        默认是 `false`,那时插件**"activates with no tools"** —— 表现成
        「模型说查不到东西」,和「工具压根没挂上」在日志里分不出来。
        """
        self.assertIn("failOnStartupError: true", self.text)

    def test_viewer_identity_goes_into_the_tool_env(self):
        """工具要按提问者裁剪可见性,而工具服务是独立进程 —— 身份只能走 env。"""
        for key in ("PRODUCTHUB_VIEWER_ID", "PRODUCTHUB_VIEWER_ROLE",
                    "PRODUCTHUB_VIEWER_NAME"):
            self.assertIn(f"{key}:", self.text)
        self.assertIn('"42"', self.text)
        self.assertIn('"employee"', self.text)

    def test_system_prompt_lands_in_the_patch(self):
        """我们的系统提示通过 personaPrefix 覆盖 dsh 的默认人格。"""
        self.assertIn("personaPrefix", self.text)


class PatchEscapingTests(unittest.TestCase):
    """提问者姓名是**用户可控**的,而它会被拼进这份 YAML。

    手工拼字符串迟早会在某个姓名上炸掉,而且是「大部分用户没事」的那种坏法:
    一个人把名字改成带引号或换行的样子,他那份 patch 就变形了 —— 变形的结果
    可能是**承重墙那两行被顶掉**。所以这里不是格式洁癖,是同一堵墙。
    """

    NASTY = 'a": \n- id: persistent-bash\n  disabled: false\n#\t中文'

    def setUp(self) -> None:
        self.text = ai_harness._patch_text(
            Viewer(id=1, role="employee", name=self.NASTY), "提示"
        )

    def test_nasty_name_cannot_inject_a_row(self):
        """不依赖 YAML 解析器的证明:那一行必须还是**一行**,且值原样可得。"""
        rows = [ln for ln in self.text.splitlines()
                if ln.startswith("- id: persistent-bash")]
        self.assertEqual(rows, ["- id: persistent-bash"],
                         "姓名里塞进了额外的行 —— patch 结构被改写了")
        self.assertIn("- id: persistent-bash\n  disabled: true", self.text,
                      "姓名把承重墙改成了 disabled: false")

        value = next(ln for ln in self.text.splitlines()
                     if ln.strip().startswith("PRODUCTHUB_VIEWER_NAME:")
                     ).split(":", 1)[1].strip()
        self.assertEqual(json.loads(value), self.NASTY,
                         "姓名没有原样到达工具服务的 env")

    @unittest.skipIf(_yaml is None, "没有 PyYAML(应用本身也不需要它)")
    def test_patch_parses_as_yaml_with_a_nasty_name(self):
        """有解析器时再确认一遍:整份 patch 仍然是合法 YAML,且行数没多出来。"""
        parsed = _yaml.safe_load(self.text)
        self.assertIsInstance(parsed, list)

        # insert 那一行没有 id(它是 {"insert": [...]}),所以过滤掉没有 id 的。
        by_id = {row["id"]: row for row in parsed
                 if isinstance(row, dict) and "id" in row}
        self.assertIs(by_id["persistent-bash"]["disabled"], True)
        self.assertIs(by_id["persistent-pwsh"]["disabled"], True)
        self.assertIs(by_id["session-log-deepseek"]["disabled"], True)

        inserted = next(r for r in parsed if isinstance(r, dict) and "insert" in r)
        self.assertEqual(
            inserted["insert"][0]["config"]["env"]["PRODUCTHUB_VIEWER_NAME"],
            self.NASTY,
        )

    @unittest.skipIf(_yaml is None, "没有 PyYAML(应用本身也不需要它)")
    def test_ordinary_patch_parses_as_yaml(self):
        text = ai_harness._patch_text(
            Viewer(id=42, role="employee", name="张三"), "系统提示"
        )
        parsed = _yaml.safe_load(text)
        self.assertIsInstance(parsed, list)
        ids = [r.get("id") for r in parsed if isinstance(r, dict)]
        self.assertIn("persistent-bash", ids)


# ---------------------------------------------------------------- 运行时的接线

@unittest.skipIf(deepseek_harness is None, "没装 deepseek-harness-sdk")
class HarnessStartConfigTests(unittest.TestCase):
    """`ai_harness._start` 交给运行时的那些参数。

    这里钉的是**接线**,不是行为:patch 生成得再对,只要没被传给运行时,
    承重墙就一行都不生效 —— 而应用照样跑得起来,只是工具全开。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ph-dsh-home-")
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ, {"AI_DSH_HOME": self._tmp.name}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _capture(self) -> dict:
        captured: dict = {}

        class FakeHarness:
            def __init__(self, **kwargs):
                captured.update(kwargs)

            def start(self):
                captured["started"] = True

        with mock.patch.object(deepseek_harness, "DeepSeekHarness", FakeHarness):
            ai_harness._start(Path(self._tmp.name) / "viewer-1-employee.yml")
        return captured

    def test_the_patch_file_is_actually_handed_to_the_runtime(self):
        """**patch 没传下去 = 承重墙一行都不生效。**

        这是整个安全姿态的最后一环:那三个 `disabled: true` 写在文件里,
        而文件要靠 `patches=` 才会被运行时读到。少传这个参数,应用不会报任何错,
        shell 工具会安安静静地回到模型手上。
        """
        captured = self._capture()
        self.assertTrue(captured["started"])
        self.assertEqual(
            captured["patches"], (str(Path(self._tmp.name) / "viewer-1-employee.yml"),)
        )

    def test_required_runtime_arguments_are_present(self):
        captured = self._capture()
        self.assertTrue(captured["dsh_home"])
        self.assertTrue(captured["profile"])
        self.assertTrue(captured["model"], "模型 ID 从 AI_MODEL 来,不能空")

    def test_working_directory_is_the_throwaway_workspace(self):
        """cwd 必须是那个**空目录**,不是仓库根。

        模型没有文件类工具,所以严格说这个目录它碰不到。但万一将来有人误开了某个
        文件工具,爆炸半径应当停在「一个空目录」,而不是「整个仓库」。
        """
        captured = self._capture()
        cwd = Path(captured["cwd"]).resolve()
        repo_root = Path(ai_harness.__file__).resolve().parent.parent
        self.assertNotEqual(cwd, repo_root, "cwd 指向仓库根了")
        self.assertTrue(
            str(cwd).startswith(str(Path(self._tmp.name).resolve())),
            f"工作目录 {cwd} 不在 dsh_home 下面",
        )
        self.assertEqual(list(cwd.iterdir()), [], "工作目录该是空的")

    def test_a_missing_sdk_says_what_to_install(self):
        """没装 SDK 时的报错要能看懂 —— 这整个应用不碰 AI 的页面也该起得来。"""
        with mock.patch.dict("sys.modules", {"deepseek_harness": None}):
            with self.assertRaises(RuntimeError) as ctx:
                ai_harness._start(Path("x.yml"))
        self.assertIn("deepseek-harness-sdk", str(ctx.exception))


class ViewerPoolKeyTests(unittest.TestCase):
    """实例池的缓存键。"""

    def test_role_is_part_of_the_key(self):
        """**角色必须进键。**同一个人被改了角色之后,工具服务的身份快照要跟着换 ——
        否则他会拿着旧角色继续查。那是权限泄漏,不是「缓存陈旧」这么轻。"""
        employee = ai_harness._viewer_key(Viewer(id=7, role="employee", name="甲"))
        admin = ai_harness._viewer_key(Viewer(id=7, role="admin", name="甲"))
        self.assertNotEqual(employee, admin)

    def test_name_is_not_part_of_the_key(self):
        """姓名不影响能查到什么,改个名不该导致重建实例(那要 0.6 秒)。"""
        self.assertEqual(
            ai_harness._viewer_key(Viewer(id=7, role="employee", name="甲")),
            ai_harness._viewer_key(Viewer(id=7, role="employee", name="乙")),
        )

    def test_max_tokens_is_part_of_the_key(self):
        """**单次输出上限也必须进键。**

        dsh 的 maxTokens 是 `AgentOptions` 的字段,只在建 agent 那一刻生效 ——
        每轮复用同一个 agent。所以池子里的实例是**带着一个固化的上限**的:
        不把它写进键,管理员把 4096 改成 8000 之后,旧实例会继续按 4096 服务
        最多 `_IDLE_TTL_SECONDS`(默认 10 分钟)。界面上没有任何异常,只是
        「我改了设置但没生效」—— 最难查的一类问题。
        """
        viewer = Viewer(id=7, role="employee", name="甲")
        self.assertNotEqual(
            ai_harness._viewer_key(viewer, 4096),
            ai_harness._viewer_key(viewer, 8000),
        )
        # 「没设」也要和任何具体值区分开,否则 None 和某个数字会共用实例。
        self.assertNotEqual(
            ai_harness._viewer_key(viewer, None),
            ai_harness._viewer_key(viewer, 4096),
        )
        self.assertEqual(
            ai_harness._viewer_key(viewer, None),
            ai_harness._viewer_key(viewer),
            "不传与显式传 None 是同一件事",
        )

    def test_reasoning_effort_is_part_of_the_key(self):
        """思考档位同样进键 —— 理由和上面那条一样,只是触发方式不同。

        `reasoning_effort` 现在来自环境变量(改它要重启进程),所以**今天**这条
        键几乎不会真的被分开。它在这里是**防以后**:哪天档位变成界面上可调的
        (那是最自然的下一个旋钮,它和 token 上限本来就是同一类东西),
        没有这条键就会原样复现「改了设置但 10 分钟不生效」。
        """
        viewer = Viewer(id=7, role="employee", name="甲")
        self.assertNotEqual(
            ai_harness._viewer_key(viewer, None, "low"),
            ai_harness._viewer_key(viewer, None, "high"),
        )
        self.assertNotEqual(
            ai_harness._viewer_key(viewer, None, None),
            ai_harness._viewer_key(viewer, None, "high"),
            "「不给」(None)必须与任何具体档位区分开",
        )
        self.assertEqual(
            ai_harness._viewer_key(viewer, None, None),
            ai_harness._viewer_key(viewer),
            "不传与显式传 None 是同一件事",
        )


class ReasoningEffortTests(unittest.TestCase):
    """思考档位的那条链:env → `_reasoning_effort()` → SDK。

    与 `MaxTokensTests` 平行。区别在于**它刻意不校验**:合法值是 dsh 定的
    (`off` / `low` / `high` / `max`,不给就是 `high`),我们抄一份白名单到这边,
    以后 dsh 加了新档位我们就会**悄悄忽略**它 —— 部署者写了 `max`,界面上一切
    正常,而实际跑的是默认档。所以这里的断言是「原样透传」,不是「合法值通过」。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ph-dsh-home-")
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ, {"AI_DSH_HOME": self._tmp.name}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _capture(self, **kwargs) -> dict:
        captured: dict = {}

        class FakeHarness:
            def __init__(self, **kw):
                captured.update(kw)

            def start(self):
                captured["started"] = True

        with mock.patch.object(deepseek_harness, "DeepSeekHarness", FakeHarness):
            ai_harness._start(Path(self._tmp.name) / "p.yml", **kwargs)
        return captured

    def test_unset_means_none_so_the_field_is_not_sent(self):
        """不配的时候必须传 `None`,让 dsh 用它自己的默认(现在是 `high`)。

        我们自己填一个 "high" 的话,「服务端默认」就变成「我们以为的服务端默认」——
        dsh 改默认值那天,我们这边不会有任何动静。
        """
        with mock.patch.dict(os.environ, {"AI_REASONING_EFFORT": ""}, clear=False):
            self.assertIsNone(ai_harness._reasoning_effort())
        saved = os.environ.pop("AI_REASONING_EFFORT", None)

        def _restore():
            if saved is not None:
                os.environ["AI_REASONING_EFFORT"] = saved

        self.addCleanup(_restore)
        self.assertIsNone(ai_harness._reasoning_effort())
        self.assertIsNone(self._capture()["reasoning_effort"])

    def test_the_env_value_reaches_the_sdk(self):
        """整条链一次走完:env → `_reasoning_effort()` → `acquire` → `_start` → SDK。

        只钉 `_reasoning_effort()` 的返回值会漏掉「值算出来了但没人用」——所以这里
        从 `run_question` 走起,中间的池子、patch、建实例全是真代码,只把 SDK 那层
        换成假的。
        """
        seen: dict = {}

        class FakeHarness:
            def __init__(self, **kw):
                seen.update(kw)

            def start(self):
                pass

            def run(self, prompt, *, session_id=None, on_notification=None):
                return SimpleNamespace(final_response="答", finish_reason="completed")

            def close(self):
                pass

        self.addCleanup(ai_harness.shutdown_all)  # 别把实例留在池子里给下一个测试
        with mock.patch.dict(os.environ, {"AI_REASONING_EFFORT": "low"}, clear=False), \
                mock.patch.object(deepseek_harness, "DeepSeekHarness", FakeHarness):
            events = list(ai_harness.run_question(
                Viewer(id=1, role="employee", name="甲"),
                prompt="问", session_id="s1", system_prompt="提示",
            ))

        self.assertEqual(events[-1]["outcome"].content, "答")
        self.assertEqual(seen["reasoning_effort"], "low")

    def test_an_unknown_value_is_not_silently_dropped(self):
        """**这条测试守的是「不做校验」这个决定本身。**

        如果哪天有人在这里加了一份白名单(动机很正当:早点报错),下面这个值就会
        变成 `None`,而部署者看到的仍然是「启动成功」。要加校验可以,但必须同时
        想清楚「dsh 新增档位时我们怎么知道」—— 想不清楚就别加。
        """
        with mock.patch.dict(os.environ, {"AI_REASONING_EFFORT": "ultra"}, clear=False):
            self.assertEqual(ai_harness._reasoning_effort(), "ultra")
        self.assertEqual(
            self._capture(reasoning_effort="ultra")["reasoning_effort"], "ultra"
        )


class MaxTokensTests(unittest.TestCase):
    """管理员那个旋钮的**最后一环**:值真的交给 SDK 了吗。

    `tests/test_ai.py::test_max_tokens_setting_reaches_the_kernel` 钉的是
    「设置 → run_question」这一段;这里钉的是「run_question → SDK」那一段。
    两段都断了才会在界面上表现为「设了没用」,所以两段都要有。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="ph-dsh-home-")
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.dict(
            os.environ, {"AI_DSH_HOME": self._tmp.name}, clear=False
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _capture(self, **kwargs) -> dict:
        captured: dict = {}

        class FakeHarness:
            def __init__(self, **kw):
                captured.update(kw)

            def start(self):
                captured["started"] = True

        with mock.patch.object(deepseek_harness, "DeepSeekHarness", FakeHarness):
            ai_harness._start(Path(self._tmp.name) / "p.yml", **kwargs)
        return captured

    def test_max_tokens_is_handed_to_the_sdk(self):
        """**给到 `DeepSeekHarness(max_tokens=…)`,不是给到 `run()`。**

        这是 dsh 的形状:maxTokens 属于 `AgentOptions`,在 `initialize()` 时
        进请求头,之后每一轮都复用它。所以「每次提问单独指定上限」在这个内核上
        做不到 —— 能做的只是让**下一个实例**用新值,而这件事由池子的键保证
        (见 `ViewerPoolKeyTests`)。
        """
        captured = self._capture(max_tokens=1234)
        self.assertEqual(captured["max_tokens"], 1234)
        self.assertTrue(captured["started"])

    def test_omitting_it_lets_the_model_default_apply(self):
        """不传时要传 `None`(而不是 0 / 某个我们自己编的默认值)。

        `None` 在 SDK 里表示「不下发这个字段」,由 dsh 按模型目录的
        `defaultMaxTokens` 决定。我们自己编一个数字,等于把 dsh 对模型的认知
        抄一份到我们这边,以后它改了我们不知道。
        """
        self.assertIsNone(self._capture()["max_tokens"])


class WallClockTests(unittest.TestCase):
    """墙钟预算 —— **v7 剩下的唯一一道上界**。

    v6 是「轮数 + 墙钟」两道。轮数那道随内核一起没了(见 `ai_agent` 模块头),
    所以这道必须真的有效:它是唯一能让一次提问**返回**的东西。
    """

    class _BlockingHarness:
        """`run()` 不返回 —— 模拟 dsh 卡在一次很慢的上游调用上。"""

        def __init__(self) -> None:
            self.release = threading.Event()
            self.finished = threading.Event()
            self.sessions: list[str] = []

        def run(self, prompt, *, session_id=None, on_notification=None):
            self.sessions.append(session_id)
            self.release.wait(15)
            self.finished.set()
            return SimpleNamespace(final_response="", finish_reason="completed")

    def _run(self, budget: float):
        harness = self._BlockingHarness()
        self.addCleanup(harness.release.set)
        with mock.patch.object(ai_harness, "acquire",
                               lambda *a, **k: (harness, threading.Lock())):
            started = time.monotonic()
            events = list(ai_harness.run_question(
                Viewer(id=1, role="employee", name="甲"),
                prompt="问", session_id="s1", system_prompt="提示",
                budget_seconds=budget,
            ))
            elapsed = time.monotonic() - started
        return harness, events, elapsed

    def test_budget_makes_the_call_return(self):
        _, events, elapsed = self._run(0.3)
        self.assertLess(elapsed, 10, "run_question 没有在预算到点后返回")
        self.assertEqual(events[-1]["type"], "_outcome")
        self.assertTrue(events[-1]["outcome"].truncated,
                        "到点了却没标 truncated —— 调用方会把它当成一条完整的回答")

    def test_the_kernel_round_keeps_running_after_we_stop_consuming(self):
        """**如实钉住这道墙的强度:它止不住已经花出去的钱。**

        v6 到点能把上游连接关掉、当场止血;v7 的 SDK 没有取消接口,我们只是
        「不再往下消费」,dsh 那一轮会一直跑到它自己结束。所以超时的代价是
        **白花的 token**,不是卡住的请求。这条断言就是那句话本身 ——
        哪天 SDK 支持取消了,这里会红,那时改掉它是**好事**。
        """
        harness, events, _ = self._run(0.3)
        self.assertTrue(events[-1]["outcome"].truncated)
        self.assertFalse(harness.finished.is_set(),
                         "内核那一轮居然已经结束了 —— 说明 SDK 能取消了,该回来改这里")

    def test_session_id_is_made_unique_before_handing_it_to_the_kernel(self):
        """dsh **拒绝复用** session id(报 `session "…" already exists`),
        所以传给它的 id 必须是每次全新的,不能是调用方给的那个。"""
        harness, _, _ = self._run(0.3)
        self.assertEqual(len(harness.sessions), 1)
        self.assertNotEqual(harness.sessions[0], "s1")
        self.assertTrue(harness.sessions[0].startswith("s1-"))

    def test_no_budget_means_no_deadline(self):
        """不给预算就是**不设限** —— 别把 None 当成 0,那会让每次提问都立刻截断。

        要在另一个线程里消费:没有预算、内核又什么都不发时,这个生成器本来就
        应该一直等下去,所以主线程是拿不到「第一个事件」来观察的。
        """
        harness = self._BlockingHarness()
        self.addCleanup(harness.release.set)
        with mock.patch.object(ai_harness, "acquire",
                               lambda *a, **k: (harness, threading.Lock())):
            generator = ai_harness.run_question(
                Viewer(id=1, role="employee", name="甲"),
                prompt="问", session_id="s", system_prompt="提示", budget_seconds=None,
            )
            got: list[dict] = []
            finished = threading.Event()

            def consume() -> None:
                got.extend(generator)
                finished.set()

            threading.Thread(target=consume, daemon=True).start()
            self.assertFalse(finished.wait(2.0), "没有预算却自己收尾了")
            harness.release.set()  # 放开内核那一轮
            self.assertTrue(finished.wait(10), "内核结束了,这边还没收尾")

        self.assertEqual(got[-1]["type"], "_outcome")
        self.assertFalse(got[-1]["outcome"].truncated, "没人超时,不该标成截断")

    def test_worker_never_outlives_the_process(self):
        """工作线程必须是 daemon —— 否则一个卡住的上游调用会让进程关不掉。"""
        harness = self._BlockingHarness()
        self.addCleanup(harness.release.set)
        names = []
        real_thread = threading.Thread

        def spy(*args, **kwargs):
            names.append(kwargs.get("daemon"))
            return real_thread(*args, **kwargs)

        with (
            mock.patch.object(ai_harness, "acquire",
                              lambda *a, **k: (harness, threading.Lock())),
            mock.patch.object(ai_harness.threading, "Thread", spy),
        ):
            list(ai_harness.run_question(
                Viewer(id=1, role="employee", name="甲"),
                prompt="问", session_id="s", system_prompt="提示", budget_seconds=0.2,
            ))
        self.assertEqual(names, [True])


# ---------------------------------------------------------------- 事件翻译

class NoRepeatedContentTests(unittest.TestCase):
    """回归:整段回答被写过两遍(第七版线上真实发生过,用户看得见)。

    根因是**竞态**,不是简单的重复赋值:工作线程在 `harness.run()` 返回后立刻检查
    「累积为空就用 `final_response` 兜底」,而这时消费者线程还没把队列里的通知翻译完
    —— `outcome.content` 仍是空串,于是完整正文被抢先写进去;消费者随后
    `outcome.content += text`,同一段正文就有了两份。

    症状和为什么它特别难查:

      - 落库的正文是两份,**推给前端的只有一份**(SSE 那条路是干净的),所以只看
        界面会以为「模型啰嗦」,只看库会以为「前端渲染了两遍」;
      - 历史会被重放,所以下一轮模型能看见自己那份重复的回答,还会在回答里
        评论这件事(「上一条回复的内容被重复输出了一遍」);
      - `reasoning` / `tool_trace` / token 计数**都不翻倍** —— 那几条路上没有第二个
        写者。三个字段里只有一个不对,是最容易被当成「模型抽风」的形状。
    """

    def _run(self):
        class SlowPayloadNotification:
            """`payload` 要花点时间才拿得到 —— 用它把竞态**变成确定的**。

            消费者被拖住 0.3 秒,而工作线程那次赋值只需要几微秒,所以「工作线程先写」
            这个顺序每次都会发生。没有这个拖累,这条测试会时红时绿 ——
            那样它既挡不住回归,也会被人当噪声关掉。
            """

            def __init__(self, payload):
                self._payload = payload

            @property
            def payload(self):
                time.sleep(0.3)
                return self._payload

        text = "这是模型的正文。"
        notification = SlowPayloadNotification({
            "event": {
                "type": "assistant/message",
                "data": {
                    "step": 1,
                    "message": {"content": [{"type": "text", "text": text}]},
                    "usage": {},
                },
            }
        })

        class FakeHarness:
            def run(self, prompt, *, session_id=None, on_notification=None):
                on_notification(notification)
                # 兜底值和正文**故意用同一个字符串**:一旦兜底抢先写进去,
                # 结果就是「正文正文」,一眼能认出来。
                return SimpleNamespace(final_response=text, finish_reason="completed")

        with mock.patch.object(ai_harness, "acquire",
                               lambda *a, **k: (FakeHarness(), threading.Lock())):
            return list(ai_harness.run_question(
                Viewer(id=1, role="employee", name="甲"),
                prompt="问", session_id="s1", system_prompt="提示",
            ))

    def test_the_answer_is_stored_exactly_once(self):
        events = self._run()
        outcome = events[-1]["outcome"]
        self.assertEqual(outcome.content, "这是模型的正文。",
                         "正文被写了两遍 —— 兜底赋值又跑到工作线程里去了?")

    def test_what_we_stream_equals_what_we_store(self):
        """推给前端的和落库的必须是**同一份**。

        这条断言是这个 bug 真正的护栏:当时两者不一致(前端 1 份、库里 2 份),
        而两边各自看都「像是对的」。以后不管哪一层出问题,这里会红。
        """
        events = self._run()
        streamed = "".join(e["text"] for e in events if e["type"] == "content_delta")
        self.assertEqual(streamed, events[-1]["outcome"].content)


class TranslationTests(unittest.TestCase):
    """dsh 通知 → 我们前端的事件。

    下面这些 payload **是从真机上抄下来的**(见 `scripts/dsh_spike.py` 的探针输出),
    不是照文档猜的。dsh 一旦改字段名,这里应该先红 —— 那些字段名全靠人工核对,
    没有类型系统兜底。
    """

    def setUp(self) -> None:
        self.outcome = ai_harness.TurnOutcome()
        self.slots: dict[str, dict] = {}

    @staticmethod
    def _notify(data: dict):
        """真机的通知形状:`payload.event = {type, data}`。"""
        return mock.Mock(payload={"event": data})

    def test_tool_name_prefix_is_stripped(self):
        self.assertEqual(
            ai_harness._tool_name("mcp__producthub__search_posts"), "search_posts"
        )
        # 不是我们的前缀就别乱动
        self.assertEqual(ai_harness._tool_name("bash"), "bash")

    def test_arguments_come_as_a_json_string(self):
        """真机上 `arguments` 是**字符串** `"{}"`,不是对象。"""
        self.assertEqual(ai_harness._parse_args("{}"), {})
        self.assertEqual(ai_harness._parse_args('{"query": "帖子"}'), {"query": "帖子"})
        self.assertEqual(ai_harness._parse_args({"a": 1}), {"a": 1})
        # 坏 JSON 不能抛 —— 抛了整个回合就没了
        self.assertEqual(ai_harness._parse_args("{oops"), {"_raw": "{oops"})

    def test_tool_call_becomes_an_event_and_a_trace_entry(self):
        events = list(ai_harness._translate(
            self._notify({
                "type": "tool/call",
                "data": {
                    "turn": 1, "step": 1, "callId": "c1",
                    "name": "mcp__producthub__search_posts",
                    "arguments": '{"query": "帖子"}',
                },
            }),
            self.outcome, self.slots,
        ))
        self.assertEqual(events, [
            {"type": "tool", "name": "search_posts", "args": {"query": "帖子"}}
        ])
        self.assertEqual(self.outcome.trace[0]["round"], 1)
        self.assertEqual(self.outcome.trace[0]["calls"][0]["name"], "search_posts")
        self.assertIsNone(self.outcome.trace[0]["calls"][0]["ok"], "还没结果,不该有成败")

    def test_tool_result_backfills_instead_of_adding_a_row(self):
        """`tool/result` 要**回填**到那次调用上,不能另起一条 —— 前端一个工具一行。

        回填靠 `callId`:结果里**没有工具名**(`source` 只有 `{kind, callId}`),
        所以名字必须回到前面那次 `tool/call` 去认。这条一开始写错过,
        症状是轨迹上所有工具都显示成空名字。
        """
        list(ai_harness._translate(
            self._notify({"type": "tool/call", "data": {
                "step": 1, "callId": "c1",
                "name": "mcp__producthub__get_summary", "arguments": "{}",
            }}),
            self.outcome, self.slots,
        ))
        events = list(ai_harness._translate(
            self._notify({"type": "tool/result", "data": {
                "step": 1,
                "message": {
                    "source": {"kind": "tool", "callId": "c1"},
                    "content": [{
                        "type": "tool-result", "toolCallId": "c1", "isError": False,
                        "content": [{"type": "text", "text": '{"count": 3}'}],
                    }],
                },
            }}),
            self.outcome, self.slots,
        ))
        self.assertEqual(len(self.outcome.trace), 1, "结果不该新增轨迹条目")
        call = self.outcome.trace[0]["calls"][0]
        self.assertTrue(call["ok"])
        self.assertIn("count", call["preview"])
        self.assertEqual(events[0]["name"], "get_summary", "工具名要靠 callId 回查")
        self.assertTrue(events[0]["ok"])

    def test_assistant_message_accumulates_reasoning_text_and_usage(self):
        events = list(ai_harness._translate(
            self._notify({
                "type": "assistant/message",
                "data": {
                    "step": 1,
                    "message": {"content": [
                        {"type": "reasoning", "text": "想一下"},
                        {"type": "text", "text": "答案是 2。"},
                    ]},
                    "usage": {"inputTokens": 100, "outputTokens": 20,
                              "cacheReadTokens": 7, "reasoningTokens": 3},
                },
            }),
            self.outcome, self.slots,
        ))
        self.assertEqual([e["type"] for e in events],
                         ["reasoning_delta", "content_delta"])
        self.assertEqual(self.outcome.reasoning, "想一下")
        self.assertEqual(self.outcome.content, "答案是 2。")
        # 100 + 7:cacheRead 是加数,不是明细(口径与反例见 UsageTests)
        self.assertEqual(self.outcome.prompt_tokens, 107)
        self.assertEqual(self.outcome.completion_tokens, 20)
        self.assertEqual(self.outcome.trace[0]["reasoning"], "想一下")

    def test_preamble_and_answer_both_reach_the_frontend(self):
        """模型常在调工具前先说一句「我来查一下」。

        那条也是 assistant/message。如果只认最后一条(或只存 final_response),
        **用户看到的**和**库里存的**就会对不上 —— 前端明明显示了,重开页面没了。
        """
        for text in ("我来查一下。", "查到两条。"):
            list(ai_harness._translate(
                self._notify({"type": "assistant/message", "data": {
                    "step": 1, "message": {"content": [{"type": "text", "text": text}]},
                }}),
                self.outcome, self.slots,
            ))
        self.assertEqual(self.outcome.content, "我来查一下。查到两条。")

    def test_unknown_event_types_are_dropped(self):
        """认不出来就丢 —— 别把 dsh 的内部事件模型变成前端的契约。"""
        for kind in ("step/start", "step/end", "request/header", "request/context",
                     "turn/start", "turn/end", "system/message", "agent/inbox/spliced"):
            self.assertEqual(
                list(ai_harness._translate(self._notify({"type": kind, "data": {}}),
                                           self.outcome, self.slots)),
                [], f"{kind} 不该被转发",
            )

    def test_unrecognizable_notification_is_dropped(self):
        for payload in ({}, {"event": None}, {"event": "不是字典"}, None):
            self.assertEqual(
                list(ai_harness._translate(mock.Mock(payload=payload),
                                           self.outcome, self.slots)),
                [], f"{payload!r} 不该产生事件",
            )

    def test_empty_tool_call_does_not_raise(self):
        """通知的形状不受我们控制。缺字段的 `tool/call` 仍是一条 tool/call,
        只是名字和参数都空着 —— 转发出去(轨迹上多一行空的)比抛掉整轮好。
        **要钉的是「不抛」,不是「必须产出空列表」。**"""
        events = list(ai_harness._translate(
            self._notify({"type": "tool/call"}), self.outcome, self.slots))
        self.assertEqual(events, [{"type": "tool", "name": "", "args": {}}])

    def test_preview_judges_failure_by_parsing_not_substring(self):
        """成败靠**解析那段 JSON**判断,不是搜 "error" 子串。

        产品简介里出现「error」这个词是完全可能的,而把一次成功查成失败
        会让用户看到一行红色的「查询失败」。
        """
        ok, _ = ai_harness._preview(
            '<tool_result name="get_product">\n'
            '{"intro": "这里有个 error 字样"}\n</tool_result>'
        )
        self.assertTrue(ok, "正文里出现 error 字样不代表工具失败")

        ok, _ = ai_harness._preview(
            '<tool_result name="get_product">\n'
            '{"error": "帖子不存在或尚未发布"}\n</tool_result>'
        )
        self.assertFalse(ok)

        # 解析不了(比如工具返回了非 JSON)时按成功处理 —— 只是个展示用的标记,
        # 不该因为标记不出来就说人家失败。
        ok, preview = ai_harness._preview("不是 JSON")
        self.assertTrue(ok)
        self.assertEqual(preview, "不是 JSON")


class UsageTests(unittest.TestCase):
    """token 记账的口径 —— **向 dsh 的字段对齐,不自己算**。

    账本(`routers/ai.py` 的月度配额)读的是 `ai_messages.prompt_tokens /
    completion_tokens`,这两列由 `_add_usage` 从 dsh 的 `TokenUsage` 写入。
    口径错了不会报错、不会有异常,只会**静默算错钱**,而且对账时才看得出来。

    dsh 给的是:
        {inputTokens, outputTokens, totalTokens?, cacheReadTokens?,
         cacheWriteTokens?, reasoningTokens?}

    真机上量出来的等式是 `total = input + output + cacheRead` ——
    **cacheRead 是加数,不是明细**。这组测试的第一版断言的是反面
    (「cacheRead 含在 input 里」),已经就地翻转,理由写在
    `test_cache_read_is_added_to_the_prompt` 里。
    """

    def _usage(self, **fields) -> ai_harness.TurnOutcome:
        outcome = ai_harness.TurnOutcome()
        ai_harness._add_usage(outcome, fields)
        return outcome

    def test_input_and_output_map_straight_across(self):
        outcome = self._usage(inputTokens=100, outputTokens=20)
        self.assertEqual(outcome.prompt_tokens, 100)
        self.assertEqual(outcome.completion_tokens, 20)

    def test_cache_read_is_added_to_the_prompt(self):
        """**cacheRead 要加进 prompt。**这条测试以前断言的是反面。

    旧写法是 `assertEqual(outcome.prompt_tokens, 100, "别把 cacheRead 加进 prompt")`,
    依据是读 dsh 插件层 `formatCacheHitPercent` 反推出来的(它做
    `promptTokens - cacheReadTokens`,看着像「能减 ⇒ 含在里面」)。真机上量了七条
    样本,七条**全部**满足 `total = input + output + cacheRead` —— 旧断言是错的。
    留着这段说明,是因为旧写法读起来完全合理,不写清楚会有人照着改回去。

    数字直接用真机上的第一条样本,不是编出来的:那一轮模型先想了 134 个 token、
    然后调了一次工具(块类型就是 reasoning + tool-call)。

    旧口径在这一条上会把 prompt 记成 204,真值是 1228 —— **少记 6 倍**。
        """
        outcome = self._usage(
            inputTokens=204, outputTokens=163, totalTokens=1391,
            cacheReadTokens=1024, reasoningTokens=134,
        )
        self.assertEqual(outcome.prompt_tokens, 204 + 1024, "cacheRead 是加数")
        self.assertEqual(outcome.completion_tokens, 163, "reasoning 不加 —— 它含在 output 里")
        self.assertEqual(
            outcome.prompt_tokens + outcome.completion_tokens, 1391,
            "input + cacheRead + output 应当等于 totalTokens",
        )

    def test_cache_write_is_added_too_even_though_we_never_saw_it(self):
        """`cacheWriteTokens` 在真机上七条样本里**从来没非零过**。

        所以「它是加数」和「它已经含在 input 里」两种可能都解释得通,没有证据能
        分开。选了加数 —— 因为只有这样 `total = input + output + cacheRead +
        cacheWrite` 那条等式才自洽。**这个选择是没有证据支撑的**,所以单独钉一条,
        等哪天真机上出现非零值、等式又对不上,这里会跟着一起红。
        """
        outcome = self._usage(
            inputTokens=100, outputTokens=20, totalTokens=134,
            cacheReadTokens=10, cacheWriteTokens=4,
        )
        self.assertEqual(outcome.prompt_tokens, 114)
        self.assertEqual(
            outcome.prompt_tokens + outcome.completion_tokens, 134,
        )

    def test_a_broken_total_is_logged_loudly(self):
        """等式不成立时要**大声报**,不能静默算错。

        算错钱没有任何外部症状 —— 界面正常、用户无感,只有对账时才发现。
        dsh 还是 `0.1.5rc1`(预发布),这个等式是我们能拿到的最直接的探针。
        """
        with self.assertLogs("producthub.ai", level="WARNING") as caught:
            self._usage(inputTokens=100, outputTokens=20, totalTokens=999,
                        cacheReadTokens=80)
        self.assertTrue(
            any("口径" in line for line in caught.output),
            f"应当有一条口径告警,实际:{caught.output}",
        )

    def test_a_missing_total_is_fine(self):
        """`totalTokens` 是可选的 —— 没有它不该报警(真机上就不总是有)。"""
        outcome = ai_harness.TurnOutcome()
        with mock.patch.object(ai_harness.log, "warning") as warned:
            ai_harness._add_usage(outcome, {"inputTokens": 5, "outputTokens": 1})
        warned.assert_not_called()
        self.assertEqual((outcome.prompt_tokens, outcome.completion_tokens), (5, 1))

    def test_multiple_model_calls_in_one_turn_accumulate(self):
        """一轮里模型会被调用多次(每一步一次),所以要**累加**。

        只留最后一次 = 只在账单上记了一步的钱。v6 的循环是同一个道理
        (每轮 HTTP 响应的 usage 都要加)。
        """
        outcome = ai_harness.TurnOutcome()
        ai_harness._add_usage(outcome, {"inputTokens": 100, "outputTokens": 20})
        ai_harness._add_usage(outcome, {"inputTokens": 250, "outputTokens": 40})
        self.assertEqual(outcome.prompt_tokens, 350)
        self.assertEqual(outcome.completion_tokens, 60)

    def test_absent_or_null_counts_are_zero_not_a_crash(self):
        outcome = ai_harness.TurnOutcome()
        ai_harness._add_usage(outcome, {"inputTokens": None, "outputTokens": None})
        ai_harness._add_usage(outcome, {})
        self.assertEqual((outcome.prompt_tokens, outcome.completion_tokens), (0, 0))


# ---------------------------------------------------------------- MCP 工具服务

class McpIdentityTests(ApiTestCase):
    """工具服务的身份:**缺了就拒绝服务,不猜**。

    猜成管理员会泄露,猜成游客会让工具莫名其妙查不到东西 —— 拿不准提问者是谁时,
    唯一安全的做法是不回答。这条路径由配置驱动(env 由 patch 写),所以
    「配置漏了」是它最可能的失败方式,必须有测试盯着。
    """

    def test_refuses_to_serve_without_an_identity(self):
        with mock.patch.dict(os.environ, {"PRODUCTHUB_VIEWER_ID": "",
                                          "PRODUCTHUB_VIEWER_ROLE": ""}, clear=False):
            with self.assertRaises(SystemExit):
                ai_mcp_server._viewer_from_env()

    def test_refuses_when_role_is_missing(self):
        with mock.patch.dict(os.environ, {"PRODUCTHUB_VIEWER_ID": "7",
                                          "PRODUCTHUB_VIEWER_ROLE": ""}, clear=False):
            with self.assertRaises(SystemExit):
                ai_mcp_server._viewer_from_env()

    def test_refuses_non_numeric_identity(self):
        with mock.patch.dict(os.environ, {"PRODUCTHUB_VIEWER_ID": "abc",
                                          "PRODUCTHUB_VIEWER_ROLE": "employee"},
                             clear=False):
            with self.assertRaises(SystemExit):
                ai_mcp_server._viewer_from_env()

    def test_identity_is_read_from_env(self):
        with mock.patch.dict(os.environ, {
            "PRODUCTHUB_VIEWER_ID": "7", "PRODUCTHUB_VIEWER_ROLE": "employee",
            "PRODUCTHUB_VIEWER_NAME": "李四",
        }, clear=False):
            viewer = ai_mcp_server._viewer_from_env()
        self.assertEqual((viewer.id, viewer.role, viewer.name), (7, "employee", "李四"))

    def test_name_is_optional_but_id_and_role_are_not(self):
        """姓名只影响展示,缺了可以空着;id 和角色决定「能看见什么」,缺了不行。"""
        env = {"PRODUCTHUB_VIEWER_ID": "7", "PRODUCTHUB_VIEWER_ROLE": "employee"}
        with mock.patch.dict(os.environ, env, clear=False):
            os.environ.pop("PRODUCTHUB_VIEWER_NAME", None)
            self.assertEqual(ai_mcp_server._viewer_from_env().name, "")


class McpToolSurfaceTests(ApiTestCase):
    """挂出去的工具**就是** `ai_tools.TOOLS`,不多不少。"""

    def test_exposed_tools_are_exactly_the_readonly_set(self):
        """集合相等,不是「包含」。

        多一个 = 有工具绕过了只读边界;少一个 = 模型看得见的工具调不动。
        """
        exposed = {t["name"] for t in ai_mcp_server._tool_list()}
        self.assertEqual(exposed, {spec.name for spec in ai_tools.TOOLS})

    def test_no_shell_or_write_tool_sneaks_in(self):
        """哪怕将来有人往 `ai_tools.TOOLS` 里加了一个写工具,这条也要响。"""
        exposed = {t["name"] for t in ai_mcp_server._tool_list()}
        for forbidden in ("bash", "shell", "pwsh", "write", "edit", "read_file",
                          "delete", "create", "update"):
            self.assertNotIn(forbidden, exposed)

    def test_every_exposed_tool_is_readonly_by_name(self):
        """这 10 个的名字都是以读动词开头的。新增工具如果是写操作,名字会露出来。"""
        for spec in ai_tools.TOOLS:
            self.assertTrue(
                spec.name.startswith(("search_", "get_", "list_", "recent_")),
                f"{spec.name} 不像只读工具 —— 若确实是只读,请在这里补上它的前缀,"
                "并确认 ai_tools 模块头那条「只读」仍然成立",
            )

    def test_tool_schemas_carry_over_unchanged(self):
        """参数 schema 要从 ToolSpec **原样**带过去,否则模型不知道传什么。"""
        by_name = {t["name"]: t for t in ai_mcp_server._tool_list()}
        self.assertEqual(
            by_name["get_post"]["inputSchema"],
            ai_tools.BY_NAME["get_post"].parameters,
        )

    def test_unknown_tool_returns_an_error_envelope_not_a_crash(self):
        """模型会编工具名。编错了要回一句话,不能把管道炸了。"""
        got = ai_mcp_server._call_tool(
            Viewer(id=1, role="employee", name="x"), "definitely_not_a_tool", {}
        )
        self.assertIn("没有名为", got["content"][0]["text"])


class DraftIsUnreachableTests(ApiTestCase):
    """**「草稿进不了模型上下文」这条性质,在新架构下的证明。**

    工具服务是模型拿到站内数据的**唯一**入口(它只有那 10 个只读工具,而且
    dsh 自带的 shell 被 patch 关掉了)。所以只要它拿不到草稿,草稿就进不了
    模型上下文 —— 与提问者的角色无关。

    ⚠️ **这里有一组「阳性对照」,不是装饰。** 上一版这条测试是靠「模型收到的
    请求里没有草稿正文」来证明的,换了内核之后那个观测点没了,于是它变成
    「断言一个空字符串里不含某词」—— 永远通过,什么都没证明。
    所以下面**先证明工具确实能拿到已发布的帖子**,再去证草稿拿不到。
    少了阳性对照,一个彻底坏掉的工具也能让安全断言变绿。
    """

    SECRET = "紫色潮汐"

    def _viewer(self, role: str) -> Viewer:
        return Viewer(id=1, role=role, name="谁")

    def test_published_post_is_reachable_positive_control(self):
        """阳性对照:已发布的帖子**必须**拿得到。

        这条红了就说明工具本身坏了 —— 那么下面那条安全断言是假绿。
        """
        published = self.new_post(title="公开的帖子", status="published",
                                  body=f"正文:{self.SECRET}")
        for role in ("employee", "admin"):
            got = ai_mcp_server._call_tool(self._viewer(role), "get_post",
                                           {"post_id": published["id"]})
            self.assertIn(self.SECRET, got["content"][0]["text"],
                          f"{role} 连已发布的帖子都拿不到 —— 工具坏了")

            found = ai_mcp_server._call_tool(self._viewer(role), "search_posts",
                                             {"query": "公开的帖子"})
            self.assertIn(self.SECRET, found["content"][0]["text"])

    def test_search_does_not_return_the_draft(self):
        self.new_post(title="机密的草稿帖子", status="draft",
                      body=f"草稿正文:{self.SECRET}")
        for role in ("employee", "admin"):
            found = ai_mcp_server._call_tool(self._viewer(role), "search_posts",
                                             {"query": "机密的草稿"})
            text = found["content"][0]["text"]
            self.assertNotIn(self.SECRET, text, f"{role} 通过搜索拿到了草稿正文")
            self.assertNotIn("机密的草稿帖子", text, f"{role} 搜到了草稿标题")

    def test_get_post_refuses_the_draft_even_for_its_author_and_admins(self):
        """**作者本人和管理员也拿不到。**

        AI 的规则是「已发布」,不是界面上那套「作者与管理员看得到草稿」
        (`ai_tools._published_only` 的注释说明了为什么两条规则各写各的)。
        草稿进上下文之后会跟着回答出现在会话里,而会话是可以被翻出来的 ——
        所以这里要比界面更严。
        """
        draft = self.new_post(title="机密的草稿帖子", status="draft",
                              body=f"草稿正文:{self.SECRET}")
        author_id = self.client.get("/api/auth/me").json()["id"]
        for role in ("employee", "admin"):
            got = ai_mcp_server._call_tool(
                Viewer(id=author_id, role=role, name="作者本人"), "get_post",
                {"post_id": draft["id"]},
            )
            text = got["content"][0]["text"]
            self.assertIn("error", text, f"{role} 通过工具服务拿到了草稿")
            self.assertNotIn(self.SECRET, text)


# ---------------------------------------------------------------- 历史拼接

class ComposePromptTests(unittest.TestCase):
    """历史被拼成一段文本,作为 dsh 这次 run 的输入。"""

    def test_empty_history_composes_to_nothing(self):
        """首问时不加包装 —— 那几行提示纯属噪音。"""
        self.assertEqual(_compose_prompt([]), "")

    def test_history_is_included_with_roles(self):
        text = _compose_prompt([
            {"role": "user", "content": "站内有什么?"},
            {"role": "assistant", "content": "有两个产品。"},
            {"role": "user", "content": "第二个呢?"},
        ])
        for piece in ("站内有什么?", "有两个产品。", "第二个呢?"):
            self.assertIn(piece, text)
        self.assertIn("用户:", text)
        self.assertIn("助手:", text)
        # 最后一条是**本次提问**,要有明显分隔,好让模型知道要回答哪一条
        self.assertIn("---", text)

    def test_reasoning_is_not_replayed_into_the_prompt(self):
        """`_history` 会给助手消息带上 `reasoning_content`,但**不能拼进提示词**。

        dsh 的输入是纯文本,没有承载 reasoning 的结构;塞进去等于把「模型当时的
        思考」伪装成「对话里说过的话」,而它的措辞是给模型自己看的。
        """
        text = _compose_prompt([
            {"role": "assistant", "content": "答案是两个。",
             "reasoning_content": "内部思考:先查产品表"},
        ])
        self.assertIn("答案是两个。", text)
        self.assertNotIn("内部思考", text)

    def test_blank_content_does_not_crash(self):
        """失败/中断的助手行 content 可能是空的,历史里照样有它们(见 `_history`)。"""
        text = _compose_prompt([
            {"role": "user", "content": "问题"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "再问一次"},
        ])
        self.assertIn("问题", text)
        self.assertIn("再问一次", text)
