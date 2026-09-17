"""AI 问答(第七版):工具只读边界、SSE、落库、配额。

## 假内核怎么造(第七版换过的地方)

第六版这里假的是**网络**:`mock.patch.object(ai_client.urllib.request, "urlopen", …)`,
用一串 SSE 分片冒充 DeepSeek。第七版内核换成了 DeepSeek Harness,`ai_client` 整个
删掉了,我们这边**不再有 HTTP 请求体**可断 —— 所以注入点移到
`ai_harness.run_question`(见 `_FakeKernel`)。

⚠️ **假内核仍然去跑真的工具层**(`ai_mcp_server._call_tool`)。这不是为了省事:
本文件最重要的一条性质是「草稿进不了模型上下文」,而真机上那个观测点是
「模型收到的每一个字」——系统提示 + 提示词 + 每一次工具返回。把工具层也一起伪造掉,
那条断言就退化成「我自己编的字符串里没有草稿」,**永远通过**。
v6 的注释里那句「断在请求上,才证明工具根本没拿到那份数据」说的就是这个,
换内核换掉的是断点的位置,不是这条理由。

所以 `_FakeKernel.model_input()` 是泄露类断言**唯一**该用的取数口:
它把模型这一轮能看到的全部文字拼起来。

## 已经不在这个文件里的东西(不是漏了)

换内核真实丢掉的行为,连同它们的测试一并删掉,列在这里以免被当成漏测:

  - `test_last_round_gets_no_tools_so_it_must_answer` —— 「最后一轮不提供工具」
    是我们手写循环才有的策略,现在跑几轮归 dsh 的 agent loop 管(见 `ai_agent`
    模块头)。上界只剩墙钟一道。
  - `test_exhausted_budget_stops_before_calling_upstream` —— v7 **没有**开跑前的
    预算检查,预算耗尽也照样起 dsh 那一轮。墙钟只是让我们**不再往下消费**,
    止不住已经花出去的钱(见 `ai_harness.run_question`)。
  - `test_tool_arguments_are_reassembled_from_fragments` —— SSE 分片拼参数是 dsh
    的活了;等价的断言(真机抓到的 `arguments` 是 JSON 字符串)在
    `test_ai_kernel.py::TranslationTests` 里。
  - `test_max_tokens_setting_reaches_the_request` —— 名字换了:
    `test_max_tokens_setting_reaches_the_kernel`。dsh 的 maxTokens 是**实例级**
    配置,不是每次调用给的,所以它到达的位置是 `DeepSeekHarness(max_tokens=…)`
    而不是 HTTP 请求体 —— 断言的位置变了,这条性质没有丢。
"""
import contextlib
import dataclasses
import json
import os
import unittest
from types import SimpleNamespace
from unittest import mock

from base import ApiTestCase, _ADMIN_EMAIL

from sqlalchemy import select

from app import ai_agent, ai_harness, ai_mcp_server, ai_tools
from app.db import SessionLocal
from app.models import AiConversation, AiMessage, AiSettings, User

# 覆盖掉跑测试那台机器上可能存在的同名变量。`clear=False` 是刻意的:
# 只钉住这几个,别的(比如 PATH)不动。
#
# 第七版起 `DEEPSEEK_API_KEY` 的作用只剩「让 is_ai_configured() 为真」(门禁与
# /status),`AI_MODEL` / `AI_BASE_URL` 由 ai_harness 的 /status 读 —— 它们**不会**
# 真的上网:内核整个被 `_FakeKernel` 换掉了。
_AI_ENV = {
    "DEEPSEEK_API_KEY": "sk-test-key",
    "AI_MODEL": "deepseek-flash",
    "AI_BASE_URL": "https://api.deepseek.test",
    "AI_WALL_CLOCK_BUDGET": "240",
}


# ---------------------------------------------------------------- 假内核

def call(name: str, args: dict | None = None, *, reasoning: str = "") -> tuple:
    """脚本一步:模型调用一个工具。"""
    return ("tool", name, args or {}, reasoning)


def say(text: str, *, reasoning: str = "", usage: tuple[int, int] = (10, 5)) -> tuple:
    """脚本一步:模型说一段话。

    可以是最终回答,也可以是调工具前的开场白(「我来查一下」)—— 真机上两者
    都是独立的 `assistant/message`,所以模型会分两次说。
    """
    return ("answer", text, reasoning, usage)


def explode(detail: str = "RuntimeError: 内核炸了") -> tuple:
    """脚本一步:内核这一轮失败(`outcome.error`)。"""
    return ("fail", detail)


class _FakeKernel:
    """站在 dsh 的位置 —— 但**真的去跑我们的工具层**。

    为什么工具是真的:见模块头。`_call_tool` 就是 MCP 子进程里跑的那个函数,
    身份用**提问者本人**的 viewer —— 于是「模型能看到的字节」在测试里和在线上
    走的是同一条路径,而不是两条。
    """

    def __init__(self, script: list[tuple] | None = None, *, truncated: bool = False):
        self.script = list(script or [])
        self.truncated = truncated
        self.runs: list[dict] = []

    # ---- 被 patch 到 ai_harness.run_question 上的那个函数 ----
    def run_question(
        self,
        viewer,
        *,
        prompt,
        session_id,
        system_prompt,
        budget_seconds=None,
        max_tokens=None,
    ):
        run = {
            "viewer": viewer, "prompt": prompt, "session_id": session_id,
            "system_prompt": system_prompt, "budget_seconds": budget_seconds,
            "max_tokens": max_tokens,
            "tool_outputs": [],
        }
        self.runs.append(run)

        outcome = ai_harness.TurnOutcome()
        for step_no, step in enumerate(self.script, start=1):
            kind = step[0]

            if kind == "tool":
                _, name, args, reasoning = step
                if reasoning:
                    outcome.reasoning += reasoning
                    yield {"type": "reasoning_delta", "text": reasoning}
                yield {"type": "tool", "name": name, "args": args}

                # ← 真工具,真身份,真的查询。工具的失败由 run_tool 收敛成信封。
                text = ai_mcp_server._call_tool(viewer, name, args)["content"][0]["text"]
                run["tool_outputs"].append(text)
                ok, preview = ai_harness._preview(text)
                outcome.trace.append({
                    "round": step_no,
                    "reasoning": reasoning,
                    "calls": [{"name": name, "args": args, "ok": ok, "preview": preview}],
                })
                yield {"type": "tool_result", "name": name, "ok": ok, "preview": preview}

            elif kind == "answer":
                _, text, reasoning, (prompt_tokens, completion_tokens) = step
                if reasoning:
                    outcome.reasoning += reasoning
                    yield {"type": "reasoning_delta", "text": reasoning}
                # 真机也是**累积**:模型在调工具前说的那句也在 final_response 之外
                # (见 ai_harness 的 worker),所以这里 += 而不是 =
                outcome.content += text
                outcome.prompt_tokens += prompt_tokens
                outcome.completion_tokens += completion_tokens
                yield {"type": "content_delta", "text": text}

            elif kind == "fail":
                outcome.error = step[1]

        outcome.truncated = self.truncated
        outcome.finish_reason = "completed"
        yield {"type": "_outcome", "outcome": outcome}

    # ---- 断言用的取数口 ----
    def model_input(self, index: int = 0) -> str:
        """**模型这一轮能看到的全部文字。**泄露类断言一律断在这个上。

        系统提示 + 提示词 + 每一次工具的返回 —— 少一样就少一个泄露面。
        """
        run = self.runs[index]
        return "\n".join([run["system_prompt"], run["prompt"], *run["tool_outputs"]])

    @property
    def prompts(self) -> list[str]:
        return [run["prompt"] for run in self.runs]

    @property
    def session_ids(self) -> list[str]:
        return [run["session_id"] for run in self.runs]


class AiTestBase(ApiTestCase):
    """带假内核的基类。所有 AI 用例都从这儿起。"""

    @contextlib.contextmanager
    def fake_ai(self, script: list[tuple] | None = None, env: dict | None = None,
                *, truncated: bool = False):
        fake = _FakeKernel(script, truncated=truncated)
        with (
            mock.patch.object(ai_harness, "run_question", fake.run_question),
            mock.patch.dict(os.environ, {**_AI_ENV, **(env or {})}, clear=False),
        ):
            yield fake

    @contextlib.contextmanager
    def no_ai_key(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            yield

    @contextlib.contextmanager
    def ai_env(self, **overrides):
        """只配好环境、**不造假内核**。

        用于「应该在开流之前就被拒绝」的那一类用例 —— 它们根本走不到起内核那一步
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

    def admin_patch_ai_settings(self, max_tokens_per_call: int | None = None, **fields):
        """管理员改 AI 设置。`PUT` 收的是**整个**设置体(不是 patch),所以这里要补齐。

        只给 `max_tokens_per_call` 时其余字段用库里的现值,免得每条用例都要重复
        一串与本用例无关的数字。
        """
        body = {
            "enabled": True,
            "monthly_token_budget": None,
            "daily_questions_per_user": 20,
            "max_tokens_per_call": 4096,
        }
        body.update(fields)
        if max_tokens_per_call is not None:
            body["max_tokens_per_call"] = max_tokens_per_call
        r = self.client.put("/api/ai/settings", json=body)
        self.assertEqual(r.status_code, 200, r.text)
        return r


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

    def test_empty_query_lists_posts_newest_first(self):
        """**空查询 = 列出最近的**,不是报错(第七版联调改的,理由见 `_clean_query`)。

        真机上模型问「站上有哪些帖子」就是先拿空查询调 `search_posts` 的 —— 原来是
        `{"error": "搜索词为空"}`,于是它开始猜关键词,一轮白跑五次工具调用。
        """
        self.new_post(title="旧帖子", status="published")
        self.new_post(title="新帖子", status="published")
        with SessionLocal() as db:
            result = ai_tools.search_posts(db, viewer=self._viewer(), query="")
        self.assertNotIn("error", result)
        self.assertEqual(
            [row["title"] for row in result["results"]], ["新帖子", "旧帖子"]
        )

    def test_empty_query_still_excludes_drafts(self):
        """留空只是去掉 LIKE 那个条件 —— **只读已发布这堵墙照旧**。

        这条和 `test_search_posts_excludes_drafts` 是一对:那条钉「有查询词时」,
        这条钉「没有查询词时」。空查询是唯一一条会走到「整表扫」的路径,
        漏掉过滤的话症状最重(全部草稿一次性进上下文)。
        """
        self.new_post(title="已发布的", status="published")
        self.new_post(title="还没写完的草稿", status="draft")
        with SessionLocal() as db:
            result = ai_tools.search_posts(db, viewer=self._viewer(), query="")
        titles = [row["title"] for row in result["results"]]
        self.assertEqual(titles, ["已发布的"])

    def test_empty_query_lists_products_and_requirements(self):
        """三个 search_* 工具同一套口径,免得模型在一个上学会了、在另一个上又撞墙。"""
        self.new_product(name="甲产品")
        self.new_requirement(description="甲需求")
        with SessionLocal() as db:
            products = ai_tools.search_products(db, viewer=self._viewer(), query="")
            requirements = ai_tools.search_requirements(db, viewer=self._viewer(), query="")
        self.assertNotIn("error", products)
        self.assertIn("甲产品", [row["name"] for row in products["results"]])
        self.assertNotIn("error", requirements)
        self.assertIn("甲需求", [row["description"] for row in requirements["results"]])

    def test_list_mode_defaults_to_the_cap_not_five(self):
        """列出模式下默认给到上限(20),不是平时那 5 条。

        `count` 是**返回条数**、不是总数,所以给 5 条会让模型把「返回了 5 条」
        当成「一共 5 条」—— 那是一个说不通的答案,而且看着很像对的。
        """
        for i in range(8):
            self.new_post(title=f"第{i}篇", status="published")
        with SessionLocal() as db:
            result = ai_tools.search_posts(db, viewer=self._viewer(), query="")
        self.assertEqual(result["count"], 8)

    def test_post_like_count_is_exposed(self):
        """点赞数进工具结果 —— 界面上点赞按钮旁边就是这个数字,不是新信息。

        真机上模型答「赞数查不到」是对的:那几个工具**确实**没有这个字段。
        """
        liked = self.new_post(title="有人赞的帖子", status="published")
        self.new_post(title="没人赞的帖子", status="published")
        r = self.client.post(f"/api/blog/{liked['id']}/like")
        self.assertEqual(r.status_code, 204, r.text)

        with SessionLocal() as db:
            detail = ai_tools.get_post(db, viewer=self._viewer(), post_id=liked["id"])
            listed = ai_tools.search_posts(db, viewer=self._viewer(), query="")
        self.assertEqual(detail["like_count"], 1)
        self.assertEqual(
            {row["title"]: row["like_count"] for row in listed["results"]},
            {"有人赞的帖子": 1, "没人赞的帖子": 0},
        )

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

        断在「模型这一轮能看到的全部文字」上(`fake.model_input()`):系统提示 +
        提示词 + 每一次工具的返回。员工与管理员各问一次,两个身份下都不该出现那篇
        草稿。管理员那一半尤其重要 —— 他在界面上看得到草稿,所以「AI 也看得到」
        是一个很自然的错误实现。

        **阳性对照不是装饰。** 旧版这条靠「请求体里没有草稿」来证明;换内核后那个
        观测点没了,如果不先证明「已发布的帖子确实被搜到了」,一个彻底坏掉的工具
        (什么都搜不到)也能让安全断言变绿 —— 那正是这个文件差点掉进去的坑。
        """
        self.new_post(title="公开的帖子", status="published", body="公开正文:蓝色礁石")
        draft = self.new_post(title="机密的草稿帖子", status="draft", body="草稿正文:紫色潮汐")

        for role in ("employee", "admin"):
            with self.subTest(role=role):
                client = self.client if role == "admin" else self.user_client("d@test.local")
                conversation = self.new_ai_conversation(client=client)
                script = [
                    # 搜索命中两篇标题(草稿标题里**也有**搜索词,否则这条断言等于没测)
                    call("search_posts", {"query": "帖子"}),
                    # 再直取一次草稿 id —— 覆盖「不搜、直接按 id 拿」这条路
                    call("get_post", {"post_id": draft["id"]}),
                    say("站内有一篇公开的帖子。"),
                ]
                with self.fake_ai(script) as fake:
                    events, _ = self.ask(conversation["id"], "站内有哪些帖子?", client=client)

                self.assertIn("done", _types(events))
                seen = fake.model_input()

                # 阳性对照:已发布的确实看得见
                self.assertIn("公开的帖子", seen, "已发布的帖子该被搜到 —— 工具坏了")
                self.assertIn("蓝色礁石", seen, "已发布的**正文**该拿得到 —— 工具坏了")
                # 安全断言:草稿的标题和正文都不能进上下文
                self.assertNotIn("机密的草稿帖子", seen,
                                 f"{role} 提问时草稿**标题**进了模型上下文 —— 泄露")
                self.assertNotIn("紫色潮汐", seen,
                                 f"{role} 提问时草稿**正文**进了模型上下文 —— 泄露")

    def test_second_question_replays_the_previous_answer(self):
        """多轮:第二次提问是**全新的 HTTP 请求**,上一次的问答只能从库里读回来。

        内核那边每次都是**独立会话**(见 `ai_harness` 模块头),所以「模型还记得
        上一轮」这件事完全靠我们重放。这条挂了,表现就是「界面上历史都在,模型
        却失忆」。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("第一次的回答。", reasoning="第一轮的思考过程")]):
            events, _ = self.ask(conversation["id"], "第一个问题")
        self.assertIn("done", _types(events))

        with self.fake_ai([say("第二次的回答。")]) as fake:
            self.ask(conversation["id"], "第二个问题")

        prompt = fake.prompts[0]
        self.assertIn("第一个问题", prompt, "上一次的提问要回放")
        self.assertIn("第一次的回答。", prompt, "上一次的回答要回放")
        self.assertIn("第二个问题", prompt, "本次提问当然要在")

    def test_reasoning_is_not_replayed_into_the_prompt(self):
        """**v7 的行为变化,写下来免得被当成 bug。**

        v6 要把 `reasoning_content` 原样回放 —— 协议要求带 `tool_calls` 的助手
        消息后面跟齐 `tool` 消息,漏了 reasoning 会偶发 400。

        v7 没有这个问题了:提示词是我们自己拼的一段文本,没有任何协议结构。
        而把 reasoning 拼进去反而是**有害**的(见 `test_ai_kernel` 里那条):
        它的措辞是给模型自己看的,塞进对话文本等于把「当时的思考」伪装成
        「说过的话」。所以这一版刻意不回放。

        内容仍然**存在库里**(ThoughtChain 要显示),只是不进提示词。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("第一次的回答。", reasoning="内部思考:先查产品表")]):
            self.ask(conversation["id"], "第一个问题")

        with self.fake_ai([say("第二次的回答。")]) as fake:
            self.ask(conversation["id"], "第二个问题")

        self.assertNotIn("内部思考", fake.prompts[0])
        # 但库里要有 —— 界面上那条思考链就是从这一列来的
        self.assertEqual(self.answers(conversation["id"])[0].reasoning_content,
                         "内部思考:先查产品表")

    def test_reasoning_is_accumulated_across_blocks_and_stored(self):
        """模型会分几条消息说(reasoning 一条、正文一条、调工具前再一条),都要累积。"""
        conversation = self.new_ai_conversation()
        script = [
            call("get_summary", reasoning="先看看汇总"),
            say("站内一共三类内容。", reasoning="再作答"),
        ]
        with self.fake_ai(script):
            self.ask(conversation["id"], "站内有什么?")

        answer = self.answers(conversation["id"])[0]
        self.assertEqual(answer.reasoning_content, "先看看汇总再作答")
        self.assertEqual(answer.content, "站内一共三类内容。")
        self.assertEqual(answer.status, "done")
        self.assertEqual((answer.prompt_tokens, answer.completion_tokens), (10, 5))

    def test_absent_reasoning_stays_null(self):
        """内核压根没给 reasoning 时存 NULL —— 不是空串。

        界面靠这个区分「没有思考过程可显示」和「有思考过程但是空的」。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("回答。")]):
            self.ask(conversation["id"], "问题")
        self.assertIsNone(self.answers(conversation["id"])[0].reasoning_content)

    def test_preamble_before_a_tool_call_is_kept(self):
        """「我来查一下」也是回答的一部分,不能只留最后那条。

        v6 里这是「累积 vs final_response」的差别;v7 一样(见 `ai_harness` 的
        worker)—— 前端显示了什么,库里就该存什么。
        """
        conversation = self.new_ai_conversation()
        script = [
            say("我来查一下。"),
            call("get_summary"),
            say("查到了。"),
        ]
        with self.fake_ai(script):
            events, _ = self.ask(conversation["id"], "站内有什么?")

        self.assertEqual(_content(events), "我来查一下。查到了。")
        self.assertEqual(self.answers(conversation["id"])[0].content, "我来查一下。查到了。")

    def test_tool_failure_does_not_break_the_answer(self):
        """工具炸了要变成给模型看的错误信封,不是让整轮 500。"""
        conversation = self.new_ai_conversation()
        boom = dataclasses.replace(
            ai_tools.BY_NAME["search_posts"],
            run=lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        script = [
            call("search_posts", {"query": "x"}),
            say("查询出错了,我换个方式说。"),
        ]
        with mock.patch.dict(ai_tools.BY_NAME, {"search_posts": boom}):
            with self.fake_ai(script) as fake:
                events, response = self.ask(conversation["id"], "搜一下")

        self.assertEqual(response.status_code, 200)
        self.assertIn("done", _types(events))
        self.assertIn("error", fake.runs[0]["tool_outputs"][0])
        # 轨迹上也要如实标成失败 —— 前端靠这个把那一行标红
        result = next(e for e in events if e["type"] == "tool_result")
        self.assertFalse(result["ok"])

    def test_kernel_failure_ends_with_an_error_event(self):
        """内核失败要发一条 error 事件并把状态记成 failed,而不是把流掐断。

        ⚠️ **与 v6 的差别**:v6 会把上游自己的话(`Insufficient Balance`)透给
        浏览器;v7 只回一句通用的,真话进日志和 `ai_messages.error`。
        对终端用户少了一次信息泄漏,对管理员没损失(他能看到那一列)。
        这是刻意的,所以这里两头都断言。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([explode("AiCallError: Insufficient Balance")]):
            events, response = self.ask(conversation["id"], "问")

        self.assertEqual(response.status_code, 200, "流已经开了,错误只能在流里报")
        self.assertIn("error", _types(events))
        detail = next(e["detail"] for e in events if e["type"] == "error")
        self.assertNotIn("Insufficient Balance", detail, "上游原话不该出现在浏览器里")

        answer = self.answers(conversation["id"])[0]
        self.assertEqual(answer.status, "failed")
        self.assertEqual(answer.content, ai_agent._FAILED_NOTE)
        self.assertIn("Insufficient Balance", answer.error, "但管理员要查得到真原因")

    def test_truncated_run_is_marked_in_events_and_in_the_db(self):
        """墙钟到点时,已经拿到的内容要留着,并且**明说**它可能不完整。

        「标注必须一起落库」:一条被砍断的回答如果只是流里标了,重开页面就看不出来
        它没写完。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("前面的内容")], truncated=True):
            events, _ = self.ask(conversation["id"], "问")

        self.assertEqual(_types(events)[0], "start")
        self.assertIn("前面的内容", _content(events), "已经收到的部分要留着")
        self.assertIn(ai_agent._TRUNCATED_NOTE, _content(events))

        answer = self.answers(conversation["id"])[0]
        self.assertIn("前面的内容", answer.content)
        self.assertIn(ai_agent._TRUNCATED_NOTE, answer.content, "标注必须一起落库")

    def test_max_tokens_setting_reaches_the_kernel(self):
        """管理员那个「单次回答 token 上限」必须真的到内核里去。

        第六版这条叫 `test_max_tokens_setting_reaches_the_request`,断言的是 HTTP
        请求体里的 `max_tokens`。换了内核之后**没有请求体可断**了 —— 但它没有
        变成「做不到」:dsh 的 maxTokens 是 `AgentOptions` 的字段,由
        `DeepSeekHarness(max_tokens=…)` 给,所以到达的位置从「每个请求」变成
        「每个实例」。这条性质本身(管理员设的值真的会生效)一点没变。

        真正接在哪儿(是不是真给了 SDK、传的是不是 int)由
        `tests/test_ai_kernel.py::MaxTokensTests` 钉住 —— 这里只钉到这一层,
        两处合起来才是从头到尾。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("答。")]) as fake:
            self.ask(conversation["id"], "问")
        self.assertEqual(fake.runs[0]["max_tokens"], 4096, "默认值来自 ai_settings")

    def test_changing_max_tokens_changes_what_the_kernel_gets(self):
        """改设置 → 下一次提问带的就不是旧值。

        这条挡的是「值在某一层被缓存住了」:dsh 的 maxTokens 只在建实例时生效,
        很容易顺手把它塞进实例池的缓存键之外(那样改了设置要等最多 10 分钟才生效,
        而界面上看不出任何异常)。`ai_harness._viewer_key` 把 max_tokens 也当成了
        键的一部分,所以新值会立刻起新实例。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("一。")]) as first:
            self.ask(conversation["id"], "问")
        self.admin_patch_ai_settings(1234)
        with self.fake_ai([say("二。")]) as second:
            self.ask(conversation["id"], "再问")
        self.assertEqual(first.runs[0]["max_tokens"], 4096)
        self.assertEqual(second.runs[0]["max_tokens"], 1234)

    def test_wall_clock_budget_is_passed_to_the_kernel(self):
        """预算得真的交给内核 —— 这是 v7 剩下的**唯一**一道上界。

        v6 是「轮数 + 墙钟」两道,换内核后轮数那道没了(见 `ai_agent` 模块头)。
        所以这一个数的传递必须钉住:断了就一点上界都没有了。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("答。")], env={"AI_WALL_CLOCK_BUDGET": "37"}) as fake:
            self.ask(conversation["id"], "问")
        self.assertEqual(fake.runs[0]["budget_seconds"], 37.0)

    def test_every_question_uses_a_fresh_session_id(self):
        """**每次提问必须是全新的 session_id** —— dsh 拒绝复用。

        dsh 把会话持久化在 `$DSH_HOME/sessions` 下的 JSONL 里,撞上已存在的 id 会
        报 `session "…" already exists`。同一个会话里问两次是最容易被忽略的撞法
        (第一次的 id 已经被写进磁盘了),所以这里问两次。
        """
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("一。")]) as first:
            self.ask(conversation["id"], "第一个问题")
        with self.fake_ai([say("二。")]) as second:
            self.ask(conversation["id"], "第二个问题")

        self.assertNotEqual(first.session_ids[0], second.session_ids[0])

    def test_client_disconnect_still_records_what_was_generated(self):
        """关标签页不能白花钱 —— 已经生成的内容与状态必须落库。

        直接驱动生成器(而不是走 HTTP):TestClient 会把响应体读完,
        模拟不出「客户端中途跑了」。
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
            conversation_id, answer_id = conversation.id, answer.id
            viewer = ai_tools.Viewer.of(user)

        stream = ai_agent.stream_answer(
            conversation_id=conversation_id, assistant_message_id=answer_id, viewer=viewer
        )
        with self.fake_ai([say("生成到一半就没人听了")]):
            self.assertEqual(next(stream)["type"], "start")
            self.assertEqual(next(stream)["type"], "content_delta")
            stream.close()  # ← 客户端跑了

        with SessionLocal() as db:
            stored = db.get(AiMessage, answer_id)
            self.assertEqual(stored.status, "interrupted")
            self.assertEqual(stored.error, "客户端断开连接")
            self.assertEqual(stored.content, "生成到一半就没人听了")
            # ⚠️ **token 用量在断连时是拿不到的**,这里如实断言 None 而不是编一个数:
            # dsh 的 usage 随整条 `assistant/message` 到达,中途跑掉就是没收到。
            # 这是已知且接受的损失(同 `sweep_stale_runs`),不是这里的 bug ——
            # 所以它被写进测试,而不是被一句「已处理」盖过去。
            self.assertIsNone(stored.prompt_tokens, "断连时 usage 还没到,不该编一个")

    def test_overlong_reasoning_and_trace_are_truncated(self):
        """reasoning 与 tool_trace 是 ai_messages 最容易长胖的两列,都要有上限。"""
        self.new_product(name="长产品", problem="很长" * 20000)
        conversation = self.new_ai_conversation()
        script = [
            call("search_products", {"query": "长产品"}),
            say("答。", reasoning="思考" * 20000),
        ]
        with self.fake_ai(script):
            self.ask(conversation["id"], "长产品是什么?")

        answer = self.answers(conversation["id"])[0]
        self.assertLess(len(answer.reasoning_content), ai_agent._MAX_REASONING_CHARS + 60)
        self.assertIn("已截断", answer.reasoning_content)

        trace = json.loads(answer.tool_trace)
        self.assertEqual(trace[0]["calls"][0]["name"], "search_products")
        self.assertLess(len(trace[0]["calls"][0]["preview"]),
                        ai_harness._TRACE_PREVIEW_CHARS + 60)


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
            self.assertEqual(ai_harness.model_name(), "deepseek-v4-pro")
            self.assertEqual(self.client.get("/api/ai/status").json()["model"], "deepseek-v4-pro")

    def test_daily_quota_blocks_before_the_stream_starts(self):
        """超限要在**开流之前**拒绝 —— 那时候还没有流,不能拿 SSE 报错。"""
        with SessionLocal() as db:
            db.add(AiSettings(id=1, daily_questions_per_user=1))
            db.commit()

        conversation = self.new_ai_conversation()
        with self.fake_ai([say("第一次回答。")]):
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

    def test_non_admin_cannot_touch_settings_or_usage(self):
        employee = self.user_client("e@test.local")
        self.assertEqual(employee.get("/api/ai/settings").status_code, 403)
        self.assertEqual(employee.put("/api/ai/settings", json={}).status_code, 403)
        self.assertEqual(employee.get("/api/ai/usage").status_code, 403)

    def test_usage_reports_numbers_without_content(self):
        """管理员看得到「谁在烧钱」,看不到「他问了什么」—— 这条界线要钉住。"""
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("秘密的回答内容。")]):
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
        with self.fake_ai([say("答。")]):
            self.ask(first["id"], "让第一个变成最近的")

        listed = self.client.get("/api/ai/conversations").json()
        self.assertEqual(listed[0]["id"], first["id"])
        self.assertEqual(listed[0]["message_count"], 2)

    def test_first_question_becomes_the_title(self):
        conversation = self.new_ai_conversation()
        self.assertEqual(conversation["title"], "新会话")
        question = "帮我查一下潮汐基石这个产品的定价策略"

        with self.fake_ai([say("答。")]) as fake:
            events, _ = self.ask(conversation["id"], question)

        self.assertEqual(next(e for e in events if e["type"] == "start")["title"], question)
        # 首问原样进提示词(没有被标题那套截断逻辑碰到)
        self.assertIn(question, fake.prompts[0])

        detail = self.client.get(f"/api/ai/conversations/{conversation['id']}").json()
        self.assertEqual(detail["title"], question)
        self.assertEqual(detail["message_count"], 2)

    def test_title_is_not_replaced_by_later_questions(self):
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("一。")]):
            self.ask(conversation["id"], "第一个问题")
        with self.fake_ai([say("二。")]) as fake:
            events, _ = self.ask(conversation["id"], "第二个完全不同的问题")

        self.assertIsNone(next(e for e in events if e["type"] == "start")["title"])
        self.assertIn("第二个完全不同的问题", fake.prompts[0])

    def test_long_first_question_is_flattened_into_the_title(self):
        """标题在侧栏只有一行:换行要压成空格,超长要截断。"""
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("答。")]):
            self.ask(conversation["id"], "第一行\n第二行" + "很长" * 100)

        title = self.client.get(f"/api/ai/conversations/{conversation['id']}").json()["title"]
        self.assertNotIn("\n", title)
        self.assertLessEqual(len(title), 30)

    def test_detail_returns_parsed_tool_trace(self):
        self.new_product(name="甲产品")
        conversation = self.new_ai_conversation()
        script = [
            call("search_products", {"query": "甲"}, reasoning="找找"),
            say("找到了。", reasoning="作答"),
        ]
        with self.fake_ai(script):
            self.ask(conversation["id"], "有甲吗")

        detail = self.client.get(f"/api/ai/conversations/{conversation['id']}").json()
        answer = detail["messages"][1]
        self.assertIsInstance(answer["tool_trace"], list, "库里的 JSON 文本要解析成结构")
        self.assertEqual(answer["tool_trace"][0]["calls"][0]["name"], "search_products")
        self.assertTrue(answer["tool_trace"][0]["calls"][0]["ok"])
        self.assertEqual(answer["reasoning_content"], "找找作答", "两轮的思考都要留下")

        # 工具的**预览**给前端就够了:完整结果是模型上下文,不是界面内容
        self.assertIn("甲产品", answer["tool_trace"][0]["calls"][0]["preview"])

    def test_tool_trace_shape_is_what_the_frontend_renders(self):
        """轨迹的形状是**前端的契约**,换内核不该动它。

        v6 的分组依据是我们自己的轮次,v7 是 dsh 的 step —— 换的是分组依据,
        形状(`round` / `reasoning` / `calls[].{name,args,ok,preview}`)保持不变,
        所以前端一行都没改。这条钉住它。
        """
        self.new_product(name="甲产品")
        conversation = self.new_ai_conversation()
        with self.fake_ai([call("search_products", {"query": "甲"}), say("找到了。")]):
            self.ask(conversation["id"], "有甲吗")

        trace = json.loads(self.answers(conversation["id"])[0].tool_trace)
        self.assertEqual(set(trace[0]), {"round", "reasoning", "calls"})
        self.assertEqual(set(trace[0]["calls"][0]), {"name", "args", "ok", "preview"})
        self.assertEqual(trace[0]["calls"][0]["args"], {"query": "甲"})

    def test_delete_conversation_removes_messages(self):
        conversation = self.new_ai_conversation()
        with self.fake_ai([say("答。")]):
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


# ================================================================ 配置查询

class AiConfigQueryTests(AiTestBase):
    """`ai_harness` 里的「配没配好 / 模型叫什么」两个查询。

    它们原来住在 `ai_client`(第六版的手写 HTTP 层)。第七版把那个模块**整个删了**
    ——它的传输层(SSE 解析、错误体、重试、分片拼参数)在换内核之后没有任何调用方,
    上游请求由 dsh 自己发。留着一份没人用的 HTTP 客户端,只会让下一个读代码的人
    以为它还在链路上。

    这两个查询留下了,因为**门禁和 /status 仍然需要它们**,而且它们与传输无关:
    只是读环境变量。
    """

    def test_unconfigured_without_a_key(self):
        with mock.patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}, clear=False):
            self.assertFalse(ai_harness.is_ai_configured())

    def test_configured_with_a_key(self):
        with mock.patch.dict(os.environ, _AI_ENV, clear=False):
            self.assertTrue(ai_harness.is_ai_configured())

    def test_the_reported_model_is_the_one_we_launch(self):
        """`/status` 报给管理员的名字必须**就是**启动时用的那个。

        这里钉的是一条真出现过的 bug:`ai_client.model_name()` 的默认值是
        `deepseek-flash`,而 `ai_harness._start` 里写的是 `deepseek-v4-flash`
        —— 没设 `AI_MODEL` 时,设置页会显示一个内核根本没用到的模型名。
        两处各留一份默认值就会这样,所以现在只有 `model_name()` 一个出处。
        """
        with mock.patch.dict(os.environ, {"AI_MODEL": ""}, clear=False):
            # 空串会让 getenv 返回 ""(不是 None),所以这里显式删掉再读
            env = dict(os.environ)
            env.pop("AI_MODEL", None)
            with mock.patch.dict(os.environ, env, clear=True):
                self.assertEqual(ai_harness.model_name(), "deepseek-flash")


# ================================================================ 启动清扫

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
        with self.fake_ai([say("答。")]):
            self.ask(conversation["id"], "问")

        self.assertEqual(ai_agent.sweep_stale_runs(), 0)
        self.assertEqual(self.answers(conversation["id"])[0].status, "done")


if __name__ == "__main__":
    unittest.main()
