"""AI 问答(第六版):工具只读边界、流式、多轮回放、配额。

## 假上游怎么造

照 tests/test_mailer.py 的先例:`mock.patch.object(ai_client.urllib.request, "urlopen", …)`。
(这就是 ai_client 里必须写 `import urllib.request` 而不能 `from urllib.request import urlopen`
的原因 —— 后者会让 patch 无对象可指,测试会**静默地打真网络**。)

假上游是**响应队列**:每次 urlopen 消费队首的一"轮",同时把请求体记下来。
两个方向都断言 —— 只看响应的话,「草稿被送进了模型」这种 bug 是看不出来的,
因为模型很可能压根没在回答里提它。**断在请求上,才证明工具根本没拿到那份数据。**

## 最重要的一条

`test_second_question_replays_reasoning_from_db`:同一个会话里问第二次。
第二次是**一个全新的 HTTP 请求**,进程内存里什么都没有 —— reasoning 只能从
`ai_messages` 里读回来。这条钉住的是 `reasoning_content` 那一列存在的全部理由;
它挂了就说明「看着偶发、其实是冷回放必现」的那个 400 会回来。
"""
import contextlib
import dataclasses
import io
import json
import os
import time
import unittest
from types import SimpleNamespace
from unittest import mock

from base import ApiTestCase, _ADMIN_EMAIL

from sqlalchemy import select

from app import ai_agent, ai_client, ai_tools
from app.db import SessionLocal
from app.models import AiConversation, AiMessage, AiSettings, User
from app.schemas import DEFAULT_MAX_TOKENS_PER_CALL

# 覆盖掉跑测试那台机器上可能存在的同名变量。`clear=False` 是刻意的:
# 只钉住这几个,别的(比如 PATH)不动。
_AI_ENV = {
    "DEEPSEEK_API_KEY": "sk-test-key",
    "AI_MODEL": "deepseek-flash",
    "AI_BASE_URL": "https://api.deepseek.test",
    "AI_WALL_CLOCK_BUDGET": "240",
    "AI_REASONING_EFFORT": "",  # 空 = 不发送(默认值),见 ai_client._build_payload
}


# ---------------------------------------------------------------- 假上游

class _FakeResponse:
    """urlopen 的返回值:一行行 SSE 字节。

    延迟发生在**迭代时**而不是造数据时 —— 这很重要:`stream_answer` 的墙钟检查
    就在 `for chunk in chunks` 的循环体里,只有边迭代边耗时才测得到它。
    造数据时睡完的话,所有延迟都落在 urlopen 之前,那是另一个场景。
    """

    def __init__(self, lines: list[bytes], *, delay_after: int = 0, delay: float = 0.0):
        self._lines = lines
        self._delay_after = delay_after
        self._delay = delay

    def __iter__(self):
        for position, line in enumerate(self._lines):
            if self._delay and position >= self._delay_after:
                time.sleep(self._delay)
            yield line

    def read(self) -> bytes:
        return b"".join(self._lines)

    def close(self) -> None:
        pass


def _sse_line(chunk: dict) -> bytes:
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n".encode("utf-8")


def _sse(chunks: list[dict], **kwargs) -> _FakeResponse:
    lines = [_sse_line(c) for c in chunks]
    lines.append(b"data: [DONE]\n")
    return _FakeResponse(lines, **kwargs)


def _delta(**fields) -> dict:
    return {"choices": [{"delta": fields}]}


def _usage(prompt: int = 10, completion: int = 5) -> dict:
    return {"choices": [], "usage": {"prompt_tokens": prompt, "completion_tokens": completion}}


def _text_turn(text: str, reasoning: str | None = None, **kwargs) -> _FakeResponse:
    """一轮纯文本回答(= 最终答案)。"""
    chunks = []
    if reasoning is not None:
        # 刻意**切成两片**发 —— 真实响应里 reasoning 是跨 chunk 累积的,
        # 一次给整段就测不出「累积」这件事。
        chunks.append(_delta(reasoning_content=reasoning[:1]))
        chunks.append(_delta(reasoning_content=reasoning[1:]))
    chunks.append(_delta(content=text))
    chunks.append(_usage())
    return _sse(chunks, **kwargs)


def _tool_turn(
    name: str, arguments: str, reasoning: str | None = None, *, pieces: int = 1
) -> _FakeResponse:
    """一轮工具调用。`pieces` 控制把 arguments 切成几段发 —— 真实响应就是碎的。"""
    chunks = []
    if reasoning is not None:
        chunks.append(_delta(reasoning_content=reasoning))
    size = max(1, len(arguments) // max(1, pieces))
    parts = [arguments[i : i + size] for i in range(0, len(arguments), size)] or [""]
    for position, part in enumerate(parts):
        call: dict = {"index": 0, "function": {"arguments": part}}
        if position == 0:
            # id 与 name 只出现在第一片上,后面几片只有 arguments(真实形状就是这样)
            call["id"] = "call_1"
            call["type"] = "function"
            call["function"]["name"] = name
        chunks.append(_delta(tool_calls=[call]))
    chunks.append(_usage())
    return _sse(chunks)


class _FakeDeepSeek:
    """响应队列 + 请求记录器。"""

    def __init__(self, turns: list[_FakeResponse]):
        self.turns = list(turns)
        self.requests: list[dict] = []

    def urlopen(self, req, timeout=None):  # noqa: ARG002 —— 签名要跟真的一样
        payload = json.loads(req.data.decode("utf-8"))
        self.requests.append(payload)
        if not self.turns:
            raise AssertionError(
                f"假上游被调用了第 {len(self.requests)} 次,但队列里已经没有响应了 —— "
                "测试给的轮数少于实际发生的轮数"
            )
        return self.turns.pop(0)

    # ---- 断言用的取数口 ----
    def messages_of(self, index: int) -> list[dict]:
        return self.requests[index]["messages"]

    def all_text(self) -> str:
        """所有请求体的全文 —— 「某段内容有没有被送出去」就断言这个。"""
        return json.dumps(self.requests, ensure_ascii=False)


class AiTestBase(ApiTestCase):
    """带假上游的基类。所有 AI 用例都从这儿起。"""

    @contextlib.contextmanager
    def fake_ai(self, turns: list[_FakeResponse], env: dict | None = None):
        fake = _FakeDeepSeek(turns)
        with (
            mock.patch.object(ai_client.urllib.request, "urlopen", fake.urlopen),
            mock.patch.dict(os.environ, {**_AI_ENV, **(env or {})}, clear=False),
        ):
            yield fake

    @contextlib.contextmanager
    def no_ai_key(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            yield

    @contextlib.contextmanager
    def ai_env(self, **overrides):
        """只配好环境、**不造假上游**。

        用于「应该在开流之前就被拒绝」的那一类用例 —— 它们根本走不到发请求那一步
        (配额、开关这些检查都排在 `is_ai_configured()` 之后),
        所以要让它们测到 429/403 而不是 503,环境里必须有一个 key。
        """
        with mock.patch.dict(os.environ, {**_AI_ENV, **overrides}, clear=False):
            yield

    def user_client(self, email: str = "emp@test.local", name: str = "普通用户"):
        """另起一个已登录的普通用户客户端。"""
        self._add_user(email=email, name=name, password="EmpTest2026")
        return self.login_client(email, "EmpTest2026")

    def ask(self, conversation_id: int, content: str = "站内有什么?", client=None):
        """POST 提问,返回 (SSE 事件列表, 原始响应)。"""
        r = (client or self.client).post(
            f"/api/ai/conversations/{conversation_id}/messages", json={"content": content}
        )
        return _events(r), r

    def stored_messages(self, conversation_id: int) -> list[AiMessage]:
        with SessionLocal() as db:
            return list(
                db.scalars(
                    select(AiMessage)
                    .where(AiMessage.conversation_id == conversation_id)
                    .order_by(AiMessage.id)
                ).all()
            )

    def answers(self, conversation_id: int) -> list[AiMessage]:
        return [m for m in self.stored_messages(conversation_id) if m.role == "assistant"]

    def admin_user(self) -> User:
        with SessionLocal() as db:
            return db.scalars(select(User).where(User.email == _ADMIN_EMAIL)).one()


def _events(response) -> list[dict]:
    """把 SSE 响应体拆成事件字典。"""
    events = []
    for line in response.text.splitlines():
        if line.startswith("data: "):
            events.append(json.loads(line[len("data: ") :]))
    return events


def _types(events: list[dict]) -> list[str]:
    return [event["type"] for event in events]


def _content(events: list[dict]) -> str:
    return "".join(e["text"] for e in events if e["type"] == "content_delta")


# ================================================================ 工具层(纯函数)

class AiToolTests(AiTestBase):
    """工具本身:只读、只读已发布、上限、转义。**这一组是泄露防线。**"""

    def _viewer(self, role: str = "employee"):
        return ai_tools.Viewer(id=1, role=role, name="某人")

    def test_search_posts_excludes_drafts(self):
        self.new_post(title="已发布的帖子", status="published")
        self.new_post(title="还没写完的草稿", status="draft")

        with SessionLocal() as db:
            result = ai_tools.search_posts(db, viewer=self._viewer(), query="的")

        titles = [row["title"] for row in result["results"]]
        self.assertIn("已发布的帖子", titles)
        self.assertNotIn("还没写完的草稿", titles)

    def test_get_post_refuses_draft_even_for_admin(self):
        """走法 A 的核心:管理员在界面上看得到草稿,问 AI 却说「没有」。"""
        draft = self.new_post(title="管理员的草稿", status="draft")

        with SessionLocal() as db:
            result = ai_tools.get_post(db, viewer=self._viewer("admin"), post_id=draft["id"])

        self.assertIn("error", result)
        self.assertNotIn("管理员的草稿", json.dumps(result, ensure_ascii=False))

    def test_search_posts_does_not_search_body(self):
        """搜索只搜 title —— 对正文做 ilike 会把整篇正文连片段一起拖进上下文。"""
        self.new_post(title="无关标题", status="published", body="正文里出现了「恐龙」这个词")
        with SessionLocal() as db:
            result = ai_tools.search_posts(db, viewer=self._viewer(), query="恐龙")
        self.assertEqual(result["count"], 0)

    def test_search_limit_is_capped(self):
        for i in range(30):
            self.new_product(name=f"产品{i:02d}")
        with SessionLocal() as db:
            result = ai_tools.search_products(db, viewer=self._viewer(), query="产品", limit=9999)
        self.assertEqual(result["count"], ai_tools._SEARCH_MAX_LIMIT)

    def test_junk_limit_falls_back(self):
        """模型给的 limit 可能是 "abc" 或负数 —— 一律夹到 [1, cap]。"""
        self.new_product(name="甲")
        with SessionLocal() as db:
            junk = ai_tools.search_products(db, viewer=self._viewer(), query="甲", limit="abc")
            negative = ai_tools.search_products(db, viewer=self._viewer(), query="甲", limit=-5)
        self.assertEqual(junk["count"], 1)
        self.assertEqual(negative["count"], 1)

    def test_long_text_is_truncated(self):
        self.new_product(name="长简介", problem="很长" * 20000)
        with SessionLocal() as db:
            result = ai_tools.search_products(db, viewer=self._viewer(), query="长简介")
        preview = result["results"][0]["problem"]
        self.assertLess(len(preview), ai_tools._PREVIEW_CHARS + 60)
        self.assertIn("已截断", preview, "截断了就要说出来,否则模型以为它看到了全文")

    def test_like_wildcards_are_escaped(self):
        """`%` 不转义会变成「后面跟任意字符」,`_` 会变成「任意一个字符」。"""
        self.new_product(name="进度 50%")
        self.new_product(name="进度 5099")
        with SessionLocal() as db:
            hit = ai_tools.search_products(db, viewer=self._viewer(), query="50%")
        names = [row["name"] for row in hit["results"]]
        self.assertIn("进度 50%", names)
        self.assertNotIn("进度 5099", names)

    def test_summary_matches_homepage(self):
        """工具的汇总必须与首页那个数字一致 —— 所以它直接调 home.get_summary。"""
        self.new_product(name="产品甲")
        self.new_post(title="已发布", status="published")
        self.new_post(title="草稿", status="draft")

        with SessionLocal() as db:
            tool = ai_tools.get_summary(db, viewer=self._viewer())
        counts = {row["key"]: row["count"] for row in tool["results"]}

        homepage = self.client.get("/api/summary").json()
        self.assertEqual(counts, {row["key"]: row["count"] for row in homepage})
        self.assertEqual(counts["blog"], 1, "博客只数已发布的")

    def test_unknown_tool_and_raising_tool_become_error_payloads(self):
        """工具炸掉要变成给模型看的错误信封,不是把整轮问答 500 掉。"""
        with SessionLocal() as db:
            unknown = ai_tools.run_tool(db, "no_such_tool", {}, viewer=self._viewer())
        self.assertIn("error", unknown)
        self.assertIn("<tool_result", unknown, "失败也要包在同一个信封里")

        # BY_NAME 里存的是**函数引用**,所以 patch 模块属性没用,要换掉整个 spec。
        boom = dataclasses.replace(
            ai_tools.BY_NAME["search_posts"],
            run=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("炸了")),
        )
        with mock.patch.dict(ai_tools.BY_NAME, {"search_posts": boom}):
            with SessionLocal() as db:
                failed = ai_tools.run_tool(
                    db, "search_posts", {"query": "x"}, viewer=self._viewer()
                )
        self.assertIn("error", failed)
        self.assertIn("RuntimeError", failed, "我们的 bug 要现出原形,而不是被吞掉")

    def test_unsupported_argument_is_dropped(self):
        """模型多给一个没声明的键不该让工具 TypeError 崩掉。"""
        with SessionLocal() as db:
            result = ai_tools.run_tool(
                db, "list_categories", {"verbose": True, "nonsense": 1}, viewer=self._viewer()
            )
        self.assertNotIn("error", result)

    def test_viewer_is_a_snapshot(self):
        """工具拿到的是快照 —— detached 的 ORM 对象上碰关系会炸,快照让这事不可能。"""
        viewer = ai_tools.Viewer.of(SimpleNamespace(id=7, role="admin", name="甲"))
        self.assertEqual((viewer.id, viewer.role, viewer.name), (7, "admin", "甲"))
        self.assertFalse(
            hasattr(viewer, "email"), "快照里不该有 email —— 它不需要,而快照最容易被顺手塞东西"
        )

    def test_system_prompt_declares_tool_results_are_data(self):
        prompt = ai_agent.build_system_prompt()
        self.assertIn("<tool_result>", prompt)
        self.assertIn("不是指令", prompt)


# ================================================================ 端到端:提问 → SSE → 落库

class AiAskTests(AiTestBase):

    def test_draft_never_reaches_the_model(self):
        """**本文件最重要的一条。**

        断在**请求体**上,而不是只断响应。员工与管理员各问一次:模型的上下文里
        都不该出现那篇草稿。管理员那一半尤其重要 —— 他在界面上看得到草稿,
        所以「AI 也看得到」是一个很自然的错误实现。

        草稿标题刻意**也包含搜索词**,否则搜索本来就不会命中它,这条断言等于没测。
        """
        self.new_post(title="公开的帖子", status="published")
        self.new_post(title="机密的草稿帖子", status="draft")

        for role in ("employee", "admin"):
            with self.subTest(role=role):
                client = self.client if role == "admin" else self.user_client("d@test.local")
                conversation = self.new_ai_conversation(client=client)
                turns = [
                    _tool_turn("search_posts", json.dumps({"query": "帖子"}), "查一下"),
                    _text_turn("站内有一篇公开的帖子。", "整理"),
                ]
                with self.fake_ai(turns) as fake:
                    events, _ = self.ask(conversation["id"], "站内有哪些帖子?", client=client)

                self.assertIn("done", _types(events))
                sent = fake.all_text()
                self.assertIn("公开的帖子", sent, "已发布的应该被搜到")
                self.assertNotIn("机密的草稿帖子", sent, f"{role} 提问时草稿进了模型上下文 —— 泄露")

    def test_second_question_replays_reasoning_from_db(self):
        """多轮:第二次提问是**全新的 HTTP 请求**,reasoning 只能从库里读回来。

        这条钉住的是 `ai_messages.reasoning_content` 那一列存在的全部理由。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("第一次的回答。", "第一轮的思考过程")]):
            events, _ = self.ask(conversation["id"], "第一个问题")
        self.assertIn("done", _types(events))

        with self.fake_ai([_text_turn("第二次的回答。", "第二轮的思考过程")]) as fake:
            self.ask(conversation["id"], "第二个问题")

        assistants = [m for m in fake.messages_of(0) if m["role"] == "assistant"]
        self.assertEqual(len(assistants), 1, "第一轮的最终回答应该被回放")
        self.assertEqual(assistants[0]["content"], "第一次的回答。")
        self.assertEqual(
            assistants[0].get("reasoning_content"),
            "第一轮的思考过程",
            "历史里的 reasoning_content 丢了 —— 带 tools 时这就是那个 400",
        )

    def test_empty_reasoning_is_replayed_as_empty_string(self):
        """NULL 与 '' 必须分辨着回放:一个是「没这个字段」,一个是「有但是空」。"""
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("回答。", "")]):
            self.ask(conversation["id"], "问题")

        self.assertEqual(self.answers(conversation["id"])[0].reasoning_content, "")

        with self.fake_ai([_text_turn("第二个回答。", "有思考")]) as fake:
            self.ask(conversation["id"], "追问")
        assistant = next(m for m in fake.messages_of(0) if m["role"] == "assistant")
        self.assertIn("reasoning_content", assistant, "空串也要带上这个键")
        self.assertEqual(assistant["reasoning_content"], "")

    def test_absent_reasoning_stays_absent(self):
        """协议里压根没有这个字段时,回放也不能凭空造一个键出来。"""
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("回答。", None)]):
            self.ask(conversation["id"], "问题")

        self.assertIsNone(self.answers(conversation["id"])[0].reasoning_content)

        with self.fake_ai([_text_turn("第二个回答。", None)]) as fake:
            self.ask(conversation["id"], "追问")
        assistant = next(m for m in fake.messages_of(0) if m["role"] == "assistant")
        self.assertNotIn("reasoning_content", assistant)

    def test_tool_arguments_are_reassembled_from_fragments(self):
        """工具参数是分片到达的 —— 不按 index 累积就 json.loads 不了。"""
        self.new_product(name="潮汐基石")
        conversation = self.new_ai_conversation()
        arguments = json.dumps({"query": "潮汐", "limit": 3})

        turns = [
            _tool_turn("search_products", arguments, "找找看", pieces=4),
            _text_turn("找到了潮汐基石。", "回答"),
        ]
        with self.fake_ai(turns) as fake:
            events, _ = self.ask(conversation["id"], "有没有潮汐这个产品?")

        self.assertIn("done", _types(events))
        assistant = next(m for m in fake.messages_of(1) if m["role"] == "assistant")
        sent = json.loads(assistant["tool_calls"][0]["function"]["arguments"])
        self.assertEqual(sent, {"query": "潮汐", "limit": 3})

        # 工具结果确实作为 role:"tool" 回灌了,而且两头的 id 对得上
        tool_msg = next(m for m in fake.messages_of(1) if m["role"] == "tool")
        self.assertEqual(tool_msg["tool_call_id"], assistant["tool_calls"][0]["id"])
        self.assertIn("潮汐基石", tool_msg["content"])
        self.assertIn("<tool_result", tool_msg["content"], "工具结果要包在数据分隔符里")

    def test_reasoning_is_accumulated_across_chunks_and_stored(self):
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("回答。", "一二三四五")]):
            self.ask(conversation["id"], "问题")

        answer = self.answers(conversation["id"])[0]
        self.assertEqual(answer.reasoning_content, "一二三四五", "跨 chunk 要累积完整")
        self.assertEqual(answer.content, "回答。")
        self.assertEqual(answer.status, "done")
        self.assertEqual((answer.prompt_tokens, answer.completion_tokens), (10, 5))

    def test_tool_failure_does_not_break_the_answer(self):
        """工具炸了要变成 role:"tool" 的错误载荷,不是让整轮 500。"""
        conversation = self.new_ai_conversation()
        boom = dataclasses.replace(
            ai_tools.BY_NAME["search_posts"],
            run=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        turns = [
            _tool_turn("search_posts", json.dumps({"query": "x"})),
            _text_turn("查询出错了,我换个方式说。"),
        ]
        with mock.patch.dict(ai_tools.BY_NAME, {"search_posts": boom}):
            with self.fake_ai(turns) as fake:
                events, response = self.ask(conversation["id"], "搜一下")

        self.assertEqual(response.status_code, 200)
        self.assertIn("done", _types(events))
        tool_msg = next(m for m in fake.messages_of(1) if m["role"] == "tool")
        self.assertIn("error", tool_msg["content"])

    def test_last_round_gets_no_tools_so_it_must_answer(self):
        """轮数用尽时**不提供工具**,模型只能就手里的东西作答 ——
        这比「轮数用尽就停下」好:用户至少得到一句话,而不是半截。"""
        self.new_product(name="甲产品")
        conversation = self.new_ai_conversation()
        call = _tool_turn("search_products", json.dumps({"query": "甲"}))
        turns = [call] * (ai_agent.MAX_ROUNDS - 1) + [_text_turn("根据已有信息,是甲产品。")]

        with self.fake_ai(turns) as fake:
            events, _ = self.ask(conversation["id"], "有甲吗?")

        self.assertEqual(len(fake.requests), ai_agent.MAX_ROUNDS)
        self.assertIn("tools", fake.requests[0])
        self.assertNotIn("tools", fake.requests[-1], "最后一轮不该再给工具")
        self.assertIn("根据已有信息", _content(events))

    def test_wall_clock_deadline_cuts_the_stream_midway(self):
        """墙钟超时要真的切断,而且要**明说**回答不完整。

        构造:第一片立刻到,第二片之前睡 1 秒 —— 而预算只有 0.3 秒。这同时钉住两件事:
        ① deadline 是在 **chunk 循环里**检查的(只在轮次开头检查的话,整段都会被读完);
        ② 已经收到的内容不丢,而是补一句「可能不完整」。
        """
        conversation = self.new_ai_conversation()
        slow = _text_turn("前面的内容", delay_after=1, delay=1.0)
        with self.fake_ai([slow], env={"AI_WALL_CLOCK_BUDGET": "0.3"}):
            events, _ = self.ask(conversation["id"], "问")

        self.assertEqual(_types(events)[0], "start")
        self.assertIn("前面的内容", _content(events), "已经收到的部分要留着")
        self.assertIn(ai_agent._TRUNCATED_NOTE, _content(events))

        answer = self.answers(conversation["id"])[0]
        self.assertIn("前面的内容", answer.content)
        self.assertIn(ai_agent._TRUNCATED_NOTE, answer.content, "标注必须一起落库")

    def test_exhausted_budget_stops_before_calling_upstream(self):
        """预算已经耗尽时连上游都不该调用 —— 不然「超时」还在花钱。"""
        conversation = self.new_ai_conversation()
        with self.fake_ai([], env={"AI_WALL_CLOCK_BUDGET": "0"}) as fake:
            events, _ = self.ask(conversation["id"], "问")

        self.assertEqual(fake.requests, [], "预算已经没了还去调上游")
        self.assertIn(ai_agent._TRUNCATED_NOTE, _content(events))

    def test_client_disconnect_still_records_what_was_generated(self):
        """关标签页不能白花钱 —— 已经生成的内容与状态必须落库。

        直接驱动生成器(而不是走 HTTP):TestClient 会把响应体读完,
        模拟不出「客户端中途跑了」。

        ⚠️ **token 用量在断连时是拿不到的**,这里如实断言 None 而不是编一个数:
        DeepSeek 只在最后一块 chunk 里带 usage(`stream_options.include_usage`),
        中途跑掉就是没收到。这是已知且接受的损失(同 ai_agent.sweep_stale_runs),
        不是这里的 bug —— 所以它被写进测试,而不是被一句「已处理」盖过去。
        """
        user = self.admin_user()
        with SessionLocal() as db:
            conversation = AiConversation(title="断线测试", created_by=user.id)
            db.add(conversation)
            db.commit()
            answer = AiMessage(
                conversation_id=conversation.id, role="assistant", content="", status="running"
            )
            db.add(answer)
            db.commit()
            conversation_id, answer_id, viewer = conversation.id, answer.id, ai_tools.Viewer.of(user)

        stream = ai_agent.stream_answer(
            conversation_id=conversation_id, assistant_message_id=answer_id, viewer=viewer
        )
        with self.fake_ai([_text_turn("生成到一半就没人听了")]):
            self.assertEqual(next(stream)["type"], "start")
            self.assertEqual(next(stream)["type"], "content_delta")
            stream.close()  # ← 客户端跑了

        with SessionLocal() as db:
            stored = db.get(AiMessage, answer_id)
            self.assertEqual(stored.status, "interrupted")
            self.assertEqual(stored.error, "客户端断开连接")
            self.assertEqual(stored.content, "生成到一半就没人听了")
            self.assertIsNone(stored.prompt_tokens, "断连时 usage 还没到,不该编一个")

    def test_overlong_reasoning_and_trace_are_truncated(self):
        """reasoning 与 tool_trace 是 ai_messages 最容易长胖的两列,都要有上限。"""
        self.new_product(name="长产品", problem="很长" * 20000)
        conversation = self.new_ai_conversation()
        turns = [
            _tool_turn("search_products", json.dumps({"query": "长产品"})),
            _text_turn("答。", "思考" * 20000),
        ]
        with self.fake_ai(turns):
            self.ask(conversation["id"], "长产品是什么?")

        answer = self.answers(conversation["id"])[0]
        self.assertLess(len(answer.reasoning_content), ai_agent._MAX_REASONING_CHARS + 60)
        self.assertIn("已截断", answer.reasoning_content)

        trace = json.loads(answer.tool_trace)
        self.assertEqual(trace[0]["calls"][0]["name"], "search_products")
        self.assertLess(len(trace[0]["calls"][0]["preview"]), ai_agent._TRACE_PREVIEW_CHARS + 60)

    def test_upstream_failure_ends_with_an_error_event(self):
        """上游挂了要发一条 error 事件并把状态记成 failed,而不是断掉连接。"""
        conversation = self.new_ai_conversation()

        def exploding_urlopen(req, timeout=None):
            raise ai_client.urllib.error.HTTPError(
                "u",
                402,
                "Payment Required",
                {},
                io.BytesIO(b'{"error":{"message":"Insufficient Balance"}}'),
            )

        with (
            mock.patch.object(ai_client.urllib.request, "urlopen", exploding_urlopen),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
        ):
            events, response = self.ask(conversation["id"], "问")

        self.assertEqual(response.status_code, 200, "流已经开了,错误只能在流里报")
        self.assertIn("error", _types(events))
        detail = next(e["detail"] for e in events if e["type"] == "error")
        self.assertIn("Insufficient Balance", detail, "上游自己的话要透出来")

        answer = self.answers(conversation["id"])[0]
        self.assertEqual(answer.status, "failed")
        self.assertEqual(answer.content, ai_agent._FAILED_NOTE)


# ================================================================ 配置 / 配额 / 权限

class AiGateTests(AiTestBase):

    def test_unconfigured_key_gives_503_and_status_says_false(self):
        conversation = self.new_ai_conversation()
        with self.no_ai_key():
            self.assertFalse(self.client.get("/api/ai/status").json()["configured"])

            r = self.client.post(
                f"/api/ai/conversations/{conversation['id']}/messages", json={"content": "你好"}
            )
            self.assertEqual(r.status_code, 503)
            self.assertIn("DEEPSEEK_API_KEY", r.json()["detail"])
            self.assertNotIn("text/event-stream", r.headers["content-type"])

    def test_status_reports_model_and_remaining(self):
        with mock.patch.dict(os.environ, _AI_ENV, clear=False):
            status = self.client.get("/api/ai/status").json()
        self.assertTrue(status["configured"])
        self.assertTrue(status["enabled"])
        self.assertEqual(status["model"], "deepseek-flash")
        self.assertEqual(status["remaining_today"], status["daily_questions_per_user"])

    def test_model_name_comes_from_env(self):
        """模型 ID 绝不硬编码 —— DeepSeek 已经改过一轮命名了。"""
        with mock.patch.dict(os.environ, {"AI_MODEL": "deepseek-v4-pro"}, clear=False):
            self.assertEqual(ai_client.model_name(), "deepseek-v4-pro")
            self.assertEqual(self.client.get("/api/ai/status").json()["model"], "deepseek-v4-pro")

    def test_daily_quota_blocks_before_the_stream_starts(self):
        """超限要在**开流之前**拒绝 —— 那时候还没有流,不能拿 SSE 报错。"""
        with SessionLocal() as db:
            db.add(AiSettings(id=1, daily_questions_per_user=1))
            db.commit()

        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("第一次回答。")]):
            events, _ = self.ask(conversation["id"], "第一问")
        self.assertIn("done", _types(events))

        with self.ai_env():
            r = self.client.post(
                f"/api/ai/conversations/{conversation['id']}/messages", json={"content": "第二问"}
            )
        self.assertEqual(r.status_code, 429)
        self.assertIn("今日提问次数已用完", r.json()["detail"])
        self.assertNotIn("text/event-stream", r.headers["content-type"], "必须是普通 JSON")

    def test_monthly_budget_blocks(self):
        user = self.admin_user()
        with SessionLocal() as db:
            db.add(AiSettings(id=1, monthly_token_budget=1))
            # 先花掉一点:一条已经记了用量的助手消息
            old = AiConversation(title="旧会话", created_by=user.id)
            db.add(old)
            db.commit()
            db.add(
                AiMessage(
                    conversation_id=old.id,
                    role="assistant",
                    content="旧回答",
                    status="done",
                    prompt_tokens=100,
                    completion_tokens=100,
                )
            )
            db.commit()

        conversation = self.new_ai_conversation()
        with self.ai_env():
            r = self.client.post(
                f"/api/ai/conversations/{conversation['id']}/messages", json={"content": "还能问吗"}
            )
        self.assertEqual(r.status_code, 429)
        self.assertIn("本月 AI 额度已用完", r.json()["detail"])
        self.assertNotIn("text/event-stream", r.headers["content-type"])

    def test_disabled_switch_blocks(self):
        with SessionLocal() as db:
            db.add(AiSettings(id=1, enabled=False))
            db.commit()
        conversation = self.new_ai_conversation()
        with self.ai_env():
            r = self.client.post(
                f"/api/ai/conversations/{conversation['id']}/messages", json={"content": "喂"}
            )
        self.assertEqual(r.status_code, 403)
        self.assertIn("关闭", r.json()["detail"])

    def test_admin_settings_roundtrip(self):
        r = self.client.get("/api/ai/settings")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["daily_questions_per_user"], 20, "表为空时给默认值")
        self.assertIsNone(r.json()["updated_at"])

        r = self.client.put(
            "/api/ai/settings",
            json={
                "enabled": True,
                "monthly_token_budget": 5_000_000,
                "daily_questions_per_user": 3,
                "max_tokens_per_call": 2048,
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["monthly_token_budget"], 5_000_000)
        self.assertIsNotNone(r.json()["updated_at"])

        # 落到了库里,而且 /status 跟着变
        self.assertEqual(self.client.get("/api/ai/status").json()["daily_questions_per_user"], 3)

    def test_monthly_budget_can_be_set_to_unlimited(self):
        """None 是「不限」这个明确的选择,不是「还没设置」—— 要能存进去也读得回来。"""
        r = self.client.put(
            "/api/ai/settings",
            json={
                "enabled": True,
                "monthly_token_budget": None,
                "daily_questions_per_user": 20,
                "max_tokens_per_call": 4096,
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["monthly_token_budget"])

    def test_settings_reject_out_of_range_values(self):
        base = {
            "enabled": True,
            "monthly_token_budget": None,
            "daily_questions_per_user": 20,
            "max_tokens_per_call": 4096,
        }
        for field, bad in (
            ("daily_questions_per_user", 0),
            ("daily_questions_per_user", 1001),
            ("max_tokens_per_call", 100),
            ("monthly_token_budget", -1),
        ):
            with self.subTest(field=field, value=bad):
                r = self.client.put("/api/ai/settings", json={**base, field: bad})
                self.assertEqual(r.status_code, 422)

    def test_max_tokens_setting_reaches_the_request(self):
        """管理员设的 max_tokens_per_call 必须真的进请求体,否则那是个摆设。"""
        self.client.put(
            "/api/ai/settings",
            json={
                "enabled": True,
                "monthly_token_budget": None,
                "daily_questions_per_user": 20,
                "max_tokens_per_call": 1234,
            },
        )
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("好。")]) as fake:
            self.ask(conversation["id"], "问")
        self.assertEqual(fake.requests[0]["max_tokens"], 1234)

    def test_non_admin_cannot_touch_settings_or_usage(self):
        employee = self.user_client("e@test.local")
        self.assertEqual(employee.get("/api/ai/settings").status_code, 403)
        self.assertEqual(employee.put("/api/ai/settings", json={}).status_code, 403)
        self.assertEqual(employee.get("/api/ai/usage").status_code, 403)

    def test_usage_reports_numbers_without_content(self):
        """管理员看得到「谁在烧钱」,看不到「他问了什么」—— 这条界线要钉住。"""
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("秘密的回答内容。")]):
            self.ask(conversation["id"], "秘密的问题内容")

        usage = self.client.get("/api/ai/usage").json()
        self.assertEqual(usage["month_tokens_used"], 15)
        self.assertEqual(usage["users"][0]["questions_today"], 1)

        blob = json.dumps(usage, ensure_ascii=False)
        self.assertNotIn("秘密的回答内容", blob)
        self.assertNotIn("秘密的问题内容", blob)


# ================================================================ 会话 CRUD 与可见性

class AiConversationTests(AiTestBase):

    def test_conversations_are_private_even_from_admins(self):
        """管理员看不到别人的会话,而且拿到的是 404 —— 403 等于承认「有这么个会话」。"""
        employee = self.user_client("p@test.local")
        mine = self.new_ai_conversation(client=employee)

        self.assertEqual(self.client.get("/api/ai/conversations").json(), [])
        self.assertEqual(self.client.get(f"/api/ai/conversations/{mine['id']}").status_code, 404)
        self.assertEqual(
            self.client.delete(f"/api/ai/conversations/{mine['id']}").status_code, 404
        )

    def test_list_is_ordered_by_recent_activity(self):
        first = self.new_ai_conversation()
        self.new_ai_conversation()
        with self.fake_ai([_text_turn("答。")]):
            self.ask(first["id"], "让第一个变成最近的")

        listed = self.client.get("/api/ai/conversations").json()
        self.assertEqual(listed[0]["id"], first["id"])
        self.assertEqual(listed[0]["message_count"], 2)

    def test_first_question_becomes_the_title(self):
        conversation = self.new_ai_conversation()
        self.assertEqual(conversation["title"], "新会话")
        question = "帮我查一下潮汐基石这个产品的定价策略"

        with self.fake_ai([_text_turn("答。")]) as fake:
            events, _ = self.ask(conversation["id"], question)

        self.assertEqual(next(e for e in events if e["type"] == "start")["title"], question)
        # 首问原样进请求(没有被标题那套截断逻辑碰到)
        self.assertEqual(fake.messages_of(0)[1]["content"], question)

        detail = self.client.get(f"/api/ai/conversations/{conversation['id']}").json()
        self.assertEqual(detail["title"], question)
        self.assertEqual(detail["message_count"], 2)

    def test_title_is_not_replaced_by_later_questions(self):
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("一。")]):
            self.ask(conversation["id"], "第一个问题")
        with self.fake_ai([_text_turn("二。")]) as fake:
            events, _ = self.ask(conversation["id"], "第二个完全不同的问题")

        self.assertIsNone(next(e for e in events if e["type"] == "start")["title"])
        self.assertEqual(fake.messages_of(0)[-1]["content"], "第二个完全不同的问题")

    def test_long_first_question_is_flattened_into_the_title(self):
        """标题在侧栏只有一行:换行要压成空格,超长要截断。"""
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("答。")]):
            self.ask(conversation["id"], "第一行\n第二行" + "很长" * 100)

        title = self.client.get(f"/api/ai/conversations/{conversation['id']}").json()["title"]
        self.assertNotIn("\n", title)
        self.assertLessEqual(len(title), 30)

    def test_detail_returns_parsed_tool_trace(self):
        self.new_product(name="甲产品")
        conversation = self.new_ai_conversation()
        turns = [
            _tool_turn("search_products", json.dumps({"query": "甲"}), "找找"),
            _text_turn("找到了。", "作答"),
        ]
        with self.fake_ai(turns):
            self.ask(conversation["id"], "有甲吗")

        detail = self.client.get(f"/api/ai/conversations/{conversation['id']}").json()
        answer = detail["messages"][1]
        self.assertIsInstance(answer["tool_trace"], list, "库里的 JSON 文本要解析成结构")
        self.assertEqual(answer["tool_trace"][0]["calls"][0]["name"], "search_products")
        self.assertTrue(answer["tool_trace"][0]["calls"][0]["ok"])
        self.assertEqual(answer["reasoning_content"], "找找作答", "两轮的思考都要留下")

        # 工具的**预览**给前端就够了:完整结果是模型上下文,不是界面内容
        self.assertIn("甲产品", answer["tool_trace"][0]["calls"][0]["preview"])

    def test_delete_conversation_removes_messages(self):
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("答。")]):
            self.ask(conversation["id"], "问")

        r = self.client.delete(f"/api/ai/conversations/{conversation['id']}")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.stored_messages(conversation["id"]), [])
        self.assertEqual(
            self.client.get(f"/api/ai/conversations/{conversation['id']}").status_code, 404
        )

    def test_missing_conversation_is_404(self):
        self.assertEqual(self.client.get("/api/ai/conversations/99999").status_code, 404)
        r = self.client.post("/api/ai/conversations/99999/messages", json={"content": "喂"})
        self.assertEqual(r.status_code, 404)

    def test_blank_question_rejected(self):
        conversation = self.new_ai_conversation()
        r = self.client.post(
            f"/api/ai/conversations/{conversation['id']}/messages", json={"content": "   "}
        )
        self.assertEqual(r.status_code, 422)

    def test_anonymous_cannot_reach_ai(self):
        """本版不新增公开面 —— 未登录一律 401。"""
        from fastapi.testclient import TestClient

        from app.main import app

        anon = TestClient(app)
        self.assertEqual(anon.get("/api/ai/status").status_code, 401)
        self.assertEqual(anon.get("/api/ai/conversations").status_code, 401)
        self.assertEqual(anon.post("/api/ai/conversations", json={}).status_code, 401)


# ================================================================ 传输层细节

class AiClientTests(AiTestBase):
    """ai_client 本身:SSE 解析、错误体、重试策略。"""

    @staticmethod
    def _http_error(status: int, body: bytes):
        return ai_client.urllib.error.HTTPError(
            "https://api.deepseek.test", status, "err", {}, io.BytesIO(body)
        )

    def test_http_error_body_is_surfaced(self):
        """「DeepSeek 说余额为零」和「上游返回 402」是五分钟与两小时的差别。"""
        def fake_urlopen(req, timeout=None):
            raise self._http_error(402, b'{"error": {"message": "Insufficient Balance"}}')

        with (
            mock.patch.object(ai_client.urllib.request, "urlopen", fake_urlopen),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
            self.assertRaises(ai_client.AiCallError) as ctx,
        ):
            list(ai_client.stream([{"role": "user", "content": "x"}], None))

        self.assertEqual(ctx.exception.status, 402)
        self.assertIn("Insufficient Balance", ctx.exception.detail)

    def test_html_error_body_is_retried_and_keeps_the_text(self):
        """前置代理返回一页 HTML 时不能因为 json.loads 失败而丢掉原始信息。"""
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            raise self._http_error(502, b"<html>Bad Gateway</html>")

        with (
            mock.patch.object(ai_client.urllib.request, "urlopen", fake_urlopen),
            mock.patch.object(ai_client.time, "sleep", lambda *_: None),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
            self.assertRaises(ai_client.AiCallError) as ctx,
        ):
            list(ai_client.stream([{"role": "user", "content": "x"}], None))

        self.assertIn("Bad Gateway", ctx.exception.detail)
        self.assertEqual(calls["n"], ai_client._MAX_ATTEMPTS, "5xx 是可重试的")

    def test_deterministic_errors_are_not_retried(self):
        """401/402/400 重试只会烧钱并拖长响应。"""
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            raise self._http_error(401, b'{"error":{"message":"bad key"}}')

        with (
            mock.patch.object(ai_client.urllib.request, "urlopen", fake_urlopen),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
            self.assertRaises(ai_client.AiCallError),
        ):
            list(ai_client.stream([{"role": "user", "content": "x"}], None))

        self.assertEqual(calls["n"], 1, "401 不该重试")

    def test_rate_limit_is_retried_then_succeeds(self):
        calls = {"n": 0}

        def fake_urlopen(req, timeout=None):
            calls["n"] += 1
            if calls["n"] == 1:
                raise self._http_error(429, b'{"error":{"message":"slow down"}}')
            return _sse([_delta(content="第二次成功了")])

        with (
            mock.patch.object(ai_client.urllib.request, "urlopen", fake_urlopen),
            mock.patch.object(ai_client.time, "sleep", lambda *_: None),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
        ):
            chunks = list(ai_client.stream([{"role": "user", "content": "x"}], None))

        self.assertEqual(calls["n"], 2, "429 应该重试一次")
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "第二次成功了")

    def test_non_sse_body_yields_nothing(self):
        """上游回了 200 但不是 SSE(比如一页 HTML)—— 跳过,不抛异常。"""
        def fake_urlopen(req, timeout=None):
            return _FakeResponse([b"<html>not json</html>\n"])

        with (
            mock.patch.object(ai_client.urllib.request, "urlopen", fake_urlopen),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
        ):
            chunks = list(ai_client.stream([{"role": "user", "content": "x"}], None))

        self.assertEqual(chunks, [])

    def test_malformed_sse_line_is_skipped_but_the_rest_survives(self):
        """一行坏数据(空行 / 注释 / 半截 JSON)不该让整段流断掉。"""
        def fake_urlopen(req, timeout=None):
            return _FakeResponse(
                [
                    b"data: {not json\n",
                    b"\n",
                    b": keep-alive comment\n",
                    'data: {"choices":[{"delta":{"content":"好"}}]}\n'.encode("utf-8"),
                    b"data: [DONE]\n",
                    'data: {"choices":[{"delta":{"content":"不该出现"}}]}\n'.encode("utf-8"),
                ]
            )

        with (
            mock.patch.object(ai_client.urllib.request, "urlopen", fake_urlopen),
            mock.patch.dict(os.environ, _AI_ENV, clear=False),
        ):
            chunks = list(ai_client.stream([{"role": "user", "content": "x"}], None))

        self.assertEqual(len(chunks), 1, "[DONE] 之后的内容不该再被读到")
        self.assertEqual(chunks[0]["choices"][0]["delta"]["content"], "好")

    def test_no_sampling_params_are_sent(self):
        """思考模式下 temperature 之类「设了不报错但完全无效」,别写进去假装有用。"""
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("好。")]) as fake:
            self.ask(conversation["id"], "问")

        payload = fake.requests[0]
        for key in ("temperature", "presence_penalty", "frequency_penalty", "reasoning_effort"):
            self.assertNotIn(key, payload, f"{key} 不该出现 —— 默认值就是不发送")

    def test_stream_options_requested_for_usage(self):
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("好。")]) as fake:
            self.ask(conversation["id"], "问")

        payload = fake.requests[0]
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["stream_options"], {"include_usage": True})
        # max_tokens 每次都带:管理员那个旋钮有默认值(表为空时用 4096),
        # 所以它**永远**是「管理员设的数」,不存在「没设」这一档。
        self.assertEqual(payload["max_tokens"], DEFAULT_MAX_TOKENS_PER_CALL)

    def test_unconfigured_call_raises_before_any_network(self):
        """没 key 时连 urlopen 都不该被调用。"""
        def explode(req, timeout=None):
            raise AssertionError("没配 key 还敢发请求")

        with (
            mock.patch.object(ai_client.urllib.request, "urlopen", explode),
            mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False),
            self.assertRaises(ai_client.AiNotConfigured),
        ):
            list(ai_client.stream([{"role": "user", "content": "x"}], None))


class AiSweepTests(AiTestBase):
    """启动清扫:进程被杀留下的 running 行不能永远转圈。"""

    def test_sweep_marks_stale_running_rows(self):
        user = self.admin_user()
        with SessionLocal() as db:
            conversation = AiConversation(title="半截", created_by=user.id)
            db.add(conversation)
            db.commit()
            stuck = AiMessage(
                conversation_id=conversation.id,
                role="assistant",
                content="写了一半",
                status="running",
            )
            db.add(stuck)
            db.commit()
            stuck_id = stuck.id

        self.assertEqual(ai_agent.sweep_stale_runs(), 1)

        with SessionLocal() as db:
            row = db.get(AiMessage, stuck_id)
            self.assertEqual(row.status, "interrupted")
            self.assertIn("重启", row.error)
            self.assertEqual(row.content, "写了一半", "已经生成的内容不该被清掉")

    def test_sweep_leaves_finished_rows_alone(self):
        conversation = self.new_ai_conversation()
        with self.fake_ai([_text_turn("答。")]):
            self.ask(conversation["id"], "问")

        self.assertEqual(ai_agent.sweep_stale_runs(), 0)
        self.assertEqual(self.answers(conversation["id"])[0].status, "done")


if __name__ == "__main__":
    unittest.main()
