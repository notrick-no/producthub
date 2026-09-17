"""多人**同时**提问时的正确性 —— 全部离线,一个 token 都不花。

## 为什么单独一个文件

`test_ai.py` 假的是**最外面那一层**(把 `ai_harness.run_question` 整个换掉),
所以实例池、每实例的锁、工作线程、通知队列这些**一行都没跑到**。而并发 bug
只可能长在那一层 —— 这一版最要命的那条(整段回答被写两遍)就是这么长出来的,
详见 `/Users/notrickno/.claude/plans/snappy-hatching-shannon.md` 第八节。

所以这里的假东西**比 `_FakeKernel` 更靠里**:只替换 dsh 的 SDK 实例
(`ai_harness._start` 的返回值),外面那套真的全在跑。

## 为什么不能省掉这些用例

这一版的容量上限是**内存**:一个实例 233MB RSS(真机实测),而
**一个提问者一个实例** —— 所以「能不能并发」和「并发几个人」直接决定
Railway 要开多大。而下面这几条一旦坏掉,症状都不是报错:

  - 锁变成全局的 → 2-3 个人**排队**,表现为「AI 好慢」,没人会往并发上想;
  - 两个人共用一个实例 → 身份是 patch 注入的,**那是权限泄漏**;
  - 两条流串台 → 两个人互相看到对方的回答。

三条都不会抛异常,只会看起来「有点怪」。所以钉死在测试里。

## 这些用例**不花 token** 的原因

起实例是纯本地的(dsh runtime 是个单文件 Node 可执行体,profile 自举不联网)。
真花钱的只有 `harness.run()` 里那几趟模型往返 —— 而它整个被换成了假的。

## ⚠️ 给改这个文件的人:假实例的名字**不能**从「第几个」推

第一版写的是 `f"实例{len(self.runtimes) + 1}"`,而两个线程同时进 `_start` 时
**两边都读到 0**(读和 append 之间隔着一次调度)—— 于是两个实例都叫「实例1」,
「两个人不该串台」那条断言就随机红。这个竞态在测试替身里,不在 `ai_harness` 里,
但排查它花了半小时。

现在名字取自 **patch 文件路径**(`viewer-901-admin`):它对每个提问者唯一、
确定,而且顺带说明了「这个实例是替谁服务的」—— 正好是串台断言要的东西。
"""
import contextlib
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

from app import ai_harness, models  # noqa: E402
from app.ai_tools import Viewer  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from sqlalchemy import select  # noqa: E402

# 让门禁放行(路由里 `is_ai_configured()` 排在一切之前)。内核是假的,不会上网。
_AI_ENV = {"DEEPSEEK_API_KEY": "sk-test-key", "AI_MODEL": "deepseek-flash"}

# 两个固定的提问者。id/role 参与池子键和 patch 文件名,所以下面到处都要对得上。
JIA = Viewer(id=901, role="admin", name="甲")
YI = Viewer(id=902, role="employee", name="乙")
JIA_STEM = "viewer-901-admin"
YI_STEM = "viewer-902-employee"


class _Say:
    """一条 `assistant/message` 通知 —— 形状与真机一致(见 `_translate`)。"""

    def __init__(self, text: str, *, tokens: tuple[int, int] = (0, 0)):
        prompt_tokens, completion_tokens = tokens
        self._payload = {
            "event": {
                "type": "assistant/message",
                "data": {
                    "step": 1,
                    "message": {"content": [{"type": "text", "text": text}]},
                    "usage": {
                        "inputTokens": prompt_tokens,
                        "outputTokens": completion_tokens,
                    },
                },
            }
        }

    @property
    def payload(self) -> dict:
        # 真机上是**属性**(它是 pydantic 模型),所以这里也用属性 ——
        # 写成普通字段的话,`_translate` 里 `getattr(n, "payload", None)` 那行
        # 就测不到了。
        return self._payload


class _Record:
    """记下「谁和谁同时在跑」—— 所有并发断言的数据来源。

    `inside()` 进出时维护每个实例的活跃计数,并留下 `(key, 进入时刻, 退出时刻)`
    区间,供「有没有重叠」这种**精确**断言用(不靠 sleep 的时长去猜)。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.live: dict[str, int] = {}
        self.max_live: dict[str, int] = {}
        self.max_total = 0
        self.spans: list[tuple[str, float, float]] = []

    @contextlib.contextmanager
    def inside(self, key: str):
        with self._lock:
            self.live[key] = self.live.get(key, 0) + 1
            self.max_live[key] = max(self.max_live.get(key, 0), self.live[key])
            self.max_total = max(self.max_total, sum(self.live.values()))
            entered = time.monotonic()
        try:
            yield
        finally:
            with self._lock:
                self.live[key] -= 1
                self.spans.append((key, entered, time.monotonic()))


class _FakeRuntime:
    """站在**一个 dsh 实例**的位置。

    只实现 `acquire` 那边用到的两个口子:`run` / `close`。真正的池、锁、线程、
    队列、翻译**全是真的**。
    """

    def __init__(
        self,
        patch_path: Path,
        record: _Record,
        *,
        tokens: tuple[int, int] = (0, 0),
        hold: threading.Event | None = None,
        barrier: threading.Barrier | None = None,
        work: float = 0.0,
    ) -> None:
        # 名字取自 patch 文件(每个提问者唯一)—— 见模块头那段警告。
        self.name = patch_path.stem
        self.patch_path = patch_path
        self.patch_text = patch_path.read_text(encoding="utf-8")
        self.record = record
        self.text = f"{self.name} 的回答"
        self.tokens = tokens
        self.hold = hold
        self.barrier = barrier
        self.work = work
        self.closed = False
        self.entered = threading.Event()

    def run(self, prompt, *, session_id=None, on_notification=None):
        with self.record.inside(self.name):
            if self.barrier is not None:
                # **确定性**的关键:两个人必须都跑到这儿才能继续。要是谁被串行化了,
                # 先到的那个会在这儿等到超时 → BrokenBarrierError → 用例红,
                # 而不是「恰好没重叠所以碰巧通过」。
                self.barrier.wait()
            self.entered.set()
            if self.hold is not None:
                self.hold.wait(timeout=5)
            elif self.work:
                time.sleep(self.work)
            on_notification(_Say(self.text, tokens=self.tokens))
        return SimpleNamespace(final_response=self.text, finish_reason="completed")

    def close(self) -> None:
        self.closed = True


class _Cluster:
    """把 `_start` 换掉:不起真 dsh,只发假实例,并把每个实例记下来。"""

    def __init__(self, record: _Record, **kwargs) -> None:
        self.record = record
        self.kwargs = kwargs
        self.runtimes: list[_FakeRuntime] = []

    def __call__(self, patch_path, max_tokens=None, reasoning_effort=None):
        runtime = _FakeRuntime(patch_path, self.record, **self.kwargs)
        # append 是原子的,所以这个列表可以当「已起了几个」的计数器用;
        # 但**不能**用它给实例取名 —— 见模块头。
        self.runtimes.append(runtime)
        return runtime


class ConcurrencyTestBase(unittest.TestCase):
    """起一套「真池子 + 假实例」的环境。"""

    def setUp(self) -> None:
        self.record = _Record()
        self._tmp = tempfile.TemporaryDirectory(prefix="ph-concurrency-")
        self.addCleanup(self._tmp.cleanup)
        # 先清池子(免得留着上一轮的实例),再收临时目录。
        self.addCleanup(ai_harness.shutdown_all)

    @contextlib.contextmanager
    def cluster(self, **kwargs):
        """`ai_harness.acquire` 走真路径,只把 `_start` 换成假实例。

        `_patch_dir` 指向临时目录:池子会**真的**按 viewer 写 patch 文件(那正是
        身份注入的地方,断言要用到),但不该往仓库的 `.dsh/patches` 里拉屎。
        ⚠️ 指向的目录必须**已存在** —— `_write_patch` 只在它自己初始化时才 mkdir。
        """
        cluster = _Cluster(self.record, **kwargs)
        with (
            mock.patch.object(ai_harness, "_start", cluster),
            mock.patch.object(ai_harness, "_patch_dir", Path(self._tmp.name)),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
        ):
            yield cluster

    # ---------- 断言助手 ----------

    def drain(self, viewer: Viewer, prompt: str, session_id: str) -> ai_harness.TurnOutcome:
        """跑完一轮,返回最后的 outcome(把事件流读干净)。"""
        outcome = None
        for event in ai_harness.run_question(
            viewer, prompt=prompt, session_id=session_id,
            system_prompt="测试用系统提示",
        ):
            if event["type"] == "_outcome":
                outcome = event["outcome"]
        assert outcome is not None
        return outcome

    def wait_for_runtime(self, cluster: _Cluster, stem: str, timeout: float = 5.0):
        """等某个提问者的实例真的被起出来 —— 起实例是在另一个线程里发生的。

        直接 `cluster.runtimes[0]` 会偶发 IndexError(第一版就这么错的):
        主线程跑到那行时,工作线程可能还没进 `_start`。
        """
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for runtime in list(cluster.runtimes):
                if runtime.name == stem:
                    return runtime
            time.sleep(0.01)
        raise AssertionError(
            f"等了 {timeout} 秒也没起出 {stem},实际起了 {[r.name for r in cluster.runtimes]}"
        )


class InstanceIsolationTests(ConcurrencyTestBase):
    """**一个提问者一个实例** —— 这条同时是容量上限和权限边界。"""

    def test_two_people_get_two_separate_instances(self):
        """两个人的实例绝不能是同一个。

        实例是**身份**的载体:提问者的 id/role 是通过 patch 注入到工具子进程的
        环境变量里的(见 `_patch_text`)。共用一个实例 = 拿别人的权限查数据 ——
        不是「缓存没命中」那类问题,是权限泄漏。

        顺带钉住「每个人各自的 patch 里写了什么」:两份 patch 各自只该出现
        自己的名字。
        """
        with self.cluster() as cluster:
            self.drain(JIA, "问甲", "s-甲")
            self.drain(YI, "问乙", "s-乙")

            self.assertEqual(
                sorted(r.name for r in cluster.runtimes), [JIA_STEM, YI_STEM]
            )
            by_stem = {r.name: r for r in cluster.runtimes}
            self.assertIn("甲", by_stem[JIA_STEM].patch_text)
            self.assertNotIn("乙", by_stem[JIA_STEM].patch_text)
            self.assertIn("乙", by_stem[YI_STEM].patch_text)
            self.assertNotIn("甲", by_stem[YI_STEM].patch_text)

    def test_the_same_person_reuses_one_instance(self):
        """同一个人两次提问**复用**同一个实例 —— 否则内存按提问次数涨。

        这条是前面那个 233MB 的前提:一个提问者一个实例,不是一次提问一个实例。
        """
        with self.cluster() as cluster:
            self.drain(JIA, "第一问", "s1")
            self.drain(JIA, "第二问", "s2")
            self.assertEqual(
                [r.name for r in cluster.runtimes], [JIA_STEM],
                "第二个实例是白花的 233MB",
            )

    def test_changing_the_role_rebuilds_the_instance(self):
        """同一个人被改了角色,**必须换新实例**。

        角色也是身份(工具层按它过滤)。沿用旧实例 = 降权之后还能拿旧权限查 ——
        `_viewer_key` 把 role 放键里就是为了这个。断在 patch 正文上,而不是断在
        文件名上:文件名里 `viewer-` 这个前缀两边都有,断它等于什么都没断。
        """
        with self.cluster() as cluster:
            self.drain(JIA, "问", "s1")
            self.drain(Viewer(id=901, role="viewer", name="甲"), "问", "s2")
            self.assertEqual(len(cluster.runtimes), 2, "降权后必须重建实例")
            texts = [r.patch_text for r in cluster.runtimes]
            self.assertTrue(any("id=901, role=admin" in t for t in texts), texts)
            self.assertTrue(any("id=901, role=viewer" in t for t in texts), texts)


class SerializationTests(ConcurrencyTestBase):
    """同一个人排队,不同的人并行 —— 这两条是一对,必须一起钉。"""

    def _two_threads(self, viewers: list[Viewer]) -> list[ai_harness.TurnOutcome]:
        outcomes: list[ai_harness.TurnOutcome] = []
        errors: list[BaseException] = []
        start = threading.Barrier(len(viewers), timeout=5)

        def one(viewer: Viewer, index: int) -> None:
            try:
                start.wait()  # 尽量让两个请求同时出发
                outcomes.append(self.drain(viewer, f"问{index}", f"s{index}"))
            except BaseException as exc:  # noqa: BLE001 —— 收起来在主线程里报
                errors.append(exc)

        threads = [
            threading.Thread(target=one, args=(v, i), daemon=True)
            for i, v in enumerate(viewers)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)
        self.assertFalse(errors, f"有线程炸了:{errors}")
        return outcomes

    def test_one_person_two_tabs_run_strictly_one_at_a_time(self):
        """同一个人的两次提问**串行** —— 第二个标签页要等第一个跑完。

        这是主动选的代价(SDK 的并发安全性没验证过,赌输的代价是串话),
        所以这里钉住的是「确实串行」,不是「越快越好」。

        判据用**时间区间**:第二次进入不早于第一次退出。这比「睡一会儿看看有没有
        重叠」精确 —— 第一轮被 `hold` 卡住不放手,第二轮就算能跑也进不来,
        所以区间一定是分开的。
        """
        hold = threading.Event()
        with self.cluster(hold=hold) as cluster:
            outcomes: list[ai_harness.TurnOutcome] = []

            def tab(viewer: Viewer, prompt: str, sid: str) -> None:
                outcomes.append(self.drain(viewer, prompt, sid))

            first = threading.Thread(
                target=tab, args=(JIA, "第一问", "s1"), daemon=True
            )
            first.start()
            runtime = self.wait_for_runtime(cluster, JIA_STEM)
            self.assertTrue(runtime.entered.wait(timeout=5), "第一轮没跑起来")

            second = threading.Thread(
                target=tab, args=(JIA, "第二问", "s2"), daemon=True
            )
            second.start()
            try:
                # 给第二个标签页充分的时间去撞锁 —— 它唯一的门就是那把实例锁。
                time.sleep(0.3)
                self.assertEqual(
                    self.record.max_live.get(JIA_STEM, 0), 1,
                    "第二个标签页和第一个同时在同一个实例里跑了",
                )
            finally:
                # 无论如何都要放行:不放的话第一轮会一直卡到 hold 的超时,
                # 那条线程会活到下一个用例里去(第一版就这么污染过后续用例)。
                hold.set()
            first.join(timeout=10)
            second.join(timeout=10)

            self.assertEqual(len(outcomes), 2, "两次提问都该拿到回答")
            spans = sorted(self.record.spans, key=lambda s: s[1])
            self.assertEqual(len(spans), 2, "该正好两轮")
            self.assertGreaterEqual(
                spans[1][1], spans[0][2], "第二问在第一问还没结束时就开始跑了"
            )
            self.assertEqual(self.record.max_live[JIA_STEM], 1)
            self.assertEqual(
                sorted(o.content for o in outcomes),
                [f"{JIA_STEM} 的回答"] * 2,
            )

    def test_two_people_really_run_in_parallel(self):
        """两个人**必须能同时跑** —— 锁是按实例的,不是全局的。

        这条挡的是反方向的错:要是哪天有人把锁改成全局的(或者在 `acquire` 外层
        加了把大锁),功能上什么都不坏 —— 只是 2-3 个人开始排队,表现为
        「AI 有点慢」。没人会往并发上想,所以必须有一条用例专门守着。

        用 `Barrier(2)` 而不是睡一会儿:串行化的话先到的那个会等到超时,
        用例**必然**红,而不是「碰巧没重叠」。
        """
        barrier = threading.Barrier(2, timeout=5)
        with self.cluster(barrier=barrier) as cluster:
            outcomes = self._two_threads([JIA, YI])
            self.assertEqual(
                sorted(r.name for r in cluster.runtimes), [JIA_STEM, YI_STEM]
            )
            self.assertEqual(
                self.record.max_total, 2,
                "两个人没有同时在跑 —— 是不是有把全局锁?",
            )
            self.assertEqual(
                sorted(o.content for o in outcomes),
                [f"{JIA_STEM} 的回答", f"{YI_STEM} 的回答"],
                "两个人的回答串台了",
            )

    def test_a_run_that_blows_up_does_not_take_the_other_person_down(self):
        """一个人的一轮炸了,不该影响另一个人的那一轮。

        实例是按人分的,所以这一点**应该**天然成立 —— 但「应该」不值钱:
        真炸的时候(模型抽风 / 工具返回超大)是两个人同时在用系统的时候。
        """
        record = self.record

        class _Boom(_FakeRuntime):
            def run(self, prompt, *, session_id=None, on_notification=None):
                with record.inside(self.name):
                    raise RuntimeError("内核炸了")

        with self.cluster() as cluster:
            def start(patch_path, max_tokens=None, reasoning_effort=None):
                boom = patch_path.stem == JIA_STEM
                runtime = (_Boom if boom else _FakeRuntime)(
                    patch_path, record, work=0.1
                )
                cluster.runtimes.append(runtime)
                return runtime

            with mock.patch.object(ai_harness, "_start", start):
                outcomes = self._two_threads([JIA, YI])

        by_content = {o.content: o for o in outcomes}
        self.assertIn(f"{YI_STEM} 的回答", by_content, "另一个人的回答被带没了")
        broken = by_content[""]
        self.assertIn("RuntimeError", broken.error or "", "炸了的那轮该记下原因")


class StreamSeparationTests(ApiTestCase):
    """走**真路由、真 SSE、真落库**,两个人同时提问。

    上面那些用例断的是内核那层;这一条断的是「用户看到的东西」——
    两条流串台是这一版最坏的用户可见故障(而且历史会被重放,污染下一轮)。
    """

    def setUp(self) -> None:
        super().setUp()
        self.record = _Record()
        self._tmp = tempfile.TemporaryDirectory(prefix="ph-concurrency-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(ai_harness.shutdown_all)
        self.jia_id = self._add_user(
            email="jia@test.local", name="甲", password="JiaTest2026"
        )
        self.yi_id = self._add_user(
            email="yi@test.local", name="乙", password="YiTest2026"
        )
        # 池子的键和 patch 文件名都带**真实** id(库里的 2、3,不是上面那几个常量),
        # 所以这里按真实 id 算,别照抄 JIA_STEM/YI_STEM。
        self.jia_stem = f"viewer-{self.jia_id}-employee"
        self.yi_stem = f"viewer-{self.yi_id}-employee"

    @contextlib.contextmanager
    def cluster(self, **kwargs):
        cluster = _Cluster(self.record, **kwargs)
        with (
            mock.patch.object(ai_harness, "_start", cluster),
            mock.patch.object(ai_harness, "_patch_dir", Path(self._tmp.name)),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
        ):
            yield cluster

    def _ask(self, email: str, password: str, out: dict) -> None:
        """以某个人的身份走完整条链路,把 (会话 id, SSE 拼起来的正文) 放进 out。"""
        client = self.login_client(email, password)
        cid = client.post("/api/ai/conversations", json={}).json()["id"]
        deltas: list[str] = []
        with client.stream(
            "POST", f"/api/ai/conversations/{cid}/messages", json={"content": "你好"}
        ) as resp:
            self.assertEqual(resp.status_code, 200, resp.read()[:200])
            for line in resp.iter_lines():
                if line.startswith("data: "):
                    event = json.loads(line[6:])
                    if event["type"] == "content_delta":
                        deltas.append(event["text"])
        out[email] = (cid, "".join(deltas))

    def test_two_streams_do_not_mix(self):
        """两个人的回答必须各回各家 —— 前端、库里都不许串。

        让两轮**真的重叠**(Barrier):串台只可能在重叠时发生。假实例的正文带
        自己的 patch 名,所以「谁拿到了谁的正文」是直接可读的。
        """
        barrier = threading.Barrier(2, timeout=5)
        with self.cluster(barrier=barrier) as cluster:
            got: dict[str, tuple[int, str]] = {}
            errors: list[BaseException] = []

            def one(email: str, password: str) -> None:
                try:
                    self._ask(email, password, got)
                except BaseException as exc:  # noqa: BLE001
                    errors.append(exc)

            threads = [
                threading.Thread(
                    target=one, args=("jia@test.local", "JiaTest2026"), daemon=True
                ),
                threading.Thread(
                    target=one, args=("yi@test.local", "YiTest2026"), daemon=True
                ),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20)

            self.assertFalse(errors, f"有请求炸了:{errors}")
            self.assertEqual(
                sorted(r.name for r in cluster.runtimes),
                sorted([self.jia_stem, self.yi_stem]),
            )
            self.assertEqual(
                self.record.max_total, 2, "两轮没有重叠,这条用例就没测到东西"
            )

            self.assertEqual(
                got["jia@test.local"][1], f"{self.jia_stem} 的回答",
                f"甲看到的不对:{got}",
            )
            self.assertEqual(
                got["yi@test.local"][1], f"{self.yi_stem} 的回答",
                f"乙看到的不对:{got}",
            )

            # 库里那一行必须和本人的那条流一致,而且必须落在本人的会话里
            with SessionLocal() as db:
                for email, expected_owner in (
                    ("jia@test.local", self.jia_id),
                    ("yi@test.local", self.yi_id),
                ):
                    cid, streamed = got[email]
                    row = db.execute(
                        select(models.AiMessage.content).where(
                            models.AiMessage.conversation_id == cid,
                            models.AiMessage.role == "assistant",
                        )
                    ).scalar_one()
                    self.assertEqual(row, streamed, f"{email} 落库的和流里给的不一样")
                    owner = db.execute(
                        select(models.AiConversation.created_by).where(
                            models.AiConversation.id == cid
                        )
                    ).scalar_one()
                    self.assertEqual(owner, expected_owner, f"{email} 的会话认错了主人")


class BudgetConcurrencyTests(ApiTestCase):
    """月度预算是**软上限** —— 这条用例记录的是**现状**,不是「这样没问题」。

    ⚠️ **读这条用例的人请注意:它断言的是当前行为,不是期望行为。**

    预算的判据是「到目前为止已经花掉的 token」(`_tokens_since`),而**正在跑的那
    一轮还没写 token**(占位行是 `running`,两个 token 列还是 NULL,结束时才改写)。
    所以:

      - 就算一次只有一个人在问,预算也可能被**最后一轮**顶过去(判据是「已花」,
        不是「已花 + 这一轮最多能花多少」)—— 这是设计上的软上限;
      - 两个人**同时**问,两边都会读到同一个「已花」,于是双双放行,
        超支上界 = **并发轮数 × 单轮成本**。

    而单轮成本没有上界:一轮什么时候结束只由模型决定(dsh 没有步数上限,
    SDK 也没有取消接口,见 `ai_harness.run_question` 的核实)。

    2-3 个人的规模下,这个超支是几百到几千 token 的量级 —— 值得知道,不值得
    为它上一个复杂的预留机制。真要收紧的话,选项是「开流前按
    `max_tokens_per_call` 预留、结束时对账」,那要加列加迁移,是另一个决定。
    """

    def setUp(self) -> None:
        super().setUp()
        self.record = _Record()
        self._tmp = tempfile.TemporaryDirectory(prefix="ph-concurrency-")
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(ai_harness.shutdown_all)
        self._add_user(email="jia@test.local", name="甲", password="JiaTest2026")
        self._add_user(email="yi@test.local", name="乙", password="YiTest2026")

    def test_two_concurrent_turns_can_overshoot_the_monthly_budget(self):
        budget = 1000
        per_turn = 600  # 两个人各花 600 → 合计 1200 > 1000
        barrier = threading.Barrier(2, timeout=5)
        cluster = _Cluster(self.record, tokens=(per_turn, 0), barrier=barrier)
        statuses: dict[str, int] = {}

        def ask(email: str, password: str) -> None:
            client = self.login_client(email, password)
            cid = client.post("/api/ai/conversations", json={}).json()["id"]
            with client.stream(
                "POST", f"/api/ai/conversations/{cid}/messages", json={"content": "问"}
            ) as resp:
                statuses[email] = resp.status_code
                for _ in resp.iter_lines():
                    pass

        with (
            mock.patch.object(ai_harness, "_start", cluster),
            mock.patch.object(ai_harness, "_patch_dir", Path(self._tmp.name)),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
        ):
            r = self.client.put(
                "/api/ai/settings",
                json={
                    "enabled": True,
                    "monthly_token_budget": budget,
                    "daily_questions_per_user": 50,
                    "max_tokens_per_call": 4096,
                },
            )
            self.assertEqual(r.status_code, 200, r.text)

            threads = [
                threading.Thread(
                    target=ask, args=("jia@test.local", "JiaTest2026"), daemon=True
                ),
                threading.Thread(
                    target=ask, args=("yi@test.local", "YiTest2026"), daemon=True
                ),
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20)

        with SessionLocal() as db:
            spent = db.execute(
                select(models.AiMessage.prompt_tokens, models.AiMessage.completion_tokens)
            ).all()
        total = sum((p or 0) + (c or 0) for p, c in spent)

        # ↓↓↓ 当前行为:两个人都被放行了,合计 1200 > 预算 1000。
        self.assertEqual(sorted(statuses.values()), [200, 200], statuses)
        self.assertEqual(total, 2 * per_turn, "两个人的花费该都记上了")
        self.assertGreater(
            total, budget, "预算居然没被顶过去 —— 那说明判据变了,回来改这条用例"
        )


if __name__ == "__main__":
    unittest.main()
