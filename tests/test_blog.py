"""博客(第五版 5C):帖子 / 标签 / 点赞,以及**草稿**这条新规矩。

要紧的四件事:
  1. **草稿不可见** —— 这一版第一次出现「行存在 ≠ 可见」。列表、详情、评论接口、
     汇总计数四处都得挡住,少挡一处就是一处泄露,而且都不会报错。
  2. **发布状态的唯一出处是 published_at** —— 首次发布写一次,之后编辑不动它;
     已发布的不能退回草稿(退回去就得把「发布时间」这个事实抹掉)。
  3. **动态规则** —— 草稿的增删改一条都不记;首次发布记 create。
  4. **标签与路由顺序** —— `/blog/tags` 排在 `/blog/{post_id}` 前面,
     顺序被挪了的话标签接口会 422。
"""
import unittest

from tests.base import ApiTestCase, SessionLocal
from app import models
from app.content_types import BY_KEY

_EMPLOYEE_EMAIL = "emp@producthub.test"
_EMPLOYEE_PASSWORD = "emp-password-123"


class BlogTestCase(ApiTestCase):
    """公共装置:一个员工账号 + 一个已登录的员工客户端。"""

    def setUp(self) -> None:
        super().setUp()
        self._add_user(
            email=_EMPLOYEE_EMAIL, name="员工甲", password=_EMPLOYEE_PASSWORD
        )
        self.emp = self.login_client(_EMPLOYEE_EMAIL, _EMPLOYEE_PASSWORD)

    def _list(self, client=None, **params) -> list[dict]:
        r = (client or self.client).get("/api/blog", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _events(self) -> list[dict]:
        r = self.client.get("/api/activity")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class PostCrudTest(BlogTestCase):
    def test_create_draft(self):
        post = self.new_post("草稿一篇")
        self.assertEqual(post["status"], "draft")
        self.assertIsNone(post["published_at"])
        self.assertEqual(post["author_name"], "测试管理员")
        self.assertEqual(post["tags"], [])
        self.assertEqual(post["like_count"], 0)
        self.assertFalse(post["liked_by_me"])

    def test_create_published_in_one_shot(self):
        """写完就发:POST 直接带 status=published,不用先建草稿再发一次。"""
        post = self.new_post("直接发布", status="published")
        self.assertEqual(post["status"], "published")
        self.assertIsNotNone(post["published_at"])

    def test_get_and_patch(self):
        post = self.new_post("原标题", body="原正文")
        r = self.client.patch(
            f"/api/blog/{post['id']}", json={"title": "新标题", "body": "新正文"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["title"], "新标题")
        self.assertEqual(r.json()["body"], "新正文")

    def test_patch_absent_key_keeps_value(self):
        """PATCH 语义:请求体里没出现的键不动。"""
        post = self.new_post("标题", body="正文")
        self.client.patch(f"/api/blog/{post['id']}", json={"title": "改了"})
        got = self.client.get(f"/api/blog/{post['id']}").json()
        self.assertEqual(got["title"], "改了")
        self.assertEqual(got["body"], "正文")

    def test_empty_title_rejected(self):
        r = self.client.post("/api/blog", json={"title": "   "})
        self.assertEqual(r.status_code, 422)

    def test_delete(self):
        post = self.new_post("要删的")
        r = self.client.delete(f"/api/blog/{post['id']}")
        self.assertEqual(r.status_code, 204, r.text)
        self.assertEqual(self.client.get(f"/api/blog/{post['id']}").status_code, 404)

    def test_missing_post_is_404(self):
        self.assertEqual(self.client.get("/api/blog/99999").status_code, 404)
        self.assertEqual(self.client.patch("/api/blog/99999", json={}).status_code, 404)
        self.assertEqual(self.client.delete("/api/blog/99999").status_code, 404)

    def test_list_is_newest_first(self):
        first = self.new_post("先发", status="published")
        second = self.new_post("后发", status="published")
        rows = self._list()
        self.assertEqual([p["id"] for p in rows], [second["id"], first["id"]])


class DraftVisibilityTest(BlogTestCase):
    """草稿只有作者与管理员看得见 —— 四条路都要堵住。"""

    def test_other_employee_does_not_see_draft_in_list(self):
        self.new_post("管理员的草稿")
        self.assertEqual(self._list(client=self.emp), [])

    def test_other_employee_gets_404_on_detail(self):
        """**404 而不是 403** —— 403 等于承认「有这么一篇」。"""
        post = self.new_post("管理员的草稿")
        r = self.emp.get(f"/api/blog/{post['id']}")
        self.assertEqual(r.status_code, 404)

    def test_other_employee_cannot_edit_or_delete_draft(self):
        post = self.new_post("管理员的草稿")
        self.assertEqual(
            self.emp.patch(f"/api/blog/{post['id']}", json={"title": "改"}).status_code,
            404,
        )
        self.assertEqual(self.emp.delete(f"/api/blog/{post['id']}").status_code, 404)

    def test_author_sees_own_draft(self):
        post = self.new_post("员工甲的草稿", client=self.emp)
        self.assertEqual([p["id"] for p in self._list(client=self.emp)], [post["id"]])
        self.assertEqual(self.emp.get(f"/api/blog/{post['id']}").status_code, 200)

    def test_admin_sees_everyones_draft(self):
        post = self.new_post("员工甲的草稿", client=self.emp)
        self.assertEqual([p["id"] for p in self._list()], [post["id"]])
        self.assertEqual(self.client.get(f"/api/blog/{post['id']}").status_code, 200)

    def test_published_is_visible_to_everyone(self):
        post = self.new_post("发出来了", status="published", client=self.emp)
        self.assertEqual([p["id"] for p in self._list(client=self.emp)], [post["id"]])
        self.assertEqual(self._list()[0]["id"], post["id"])

    def test_only_author_or_admin_can_edit_published_post(self):
        """已发布的帖子大家都看得见,但改还是只能作者或管理员 —— 这里 403 合适。"""
        post = self.new_post("员工甲发的", status="published", client=self.emp)
        self.assertEqual(
            self.client.patch(f"/api/blog/{post['id']}", json={"title": "改"}).status_code,
            200,
        )
        self._add_user(
            email="other@producthub.test", name="员工乙", password=_EMPLOYEE_PASSWORD
        )
        third = self.login_client("other@producthub.test", _EMPLOYEE_PASSWORD)
        self.assertEqual(
            third.patch(f"/api/blog/{post['id']}", json={"title": "改"}).status_code, 403
        )


class PublishTest(BlogTestCase):
    def test_first_publish_sets_published_at(self):
        post = self.new_post("草稿")
        r = self.client.patch(f"/api/blog/{post['id']}", json={"status": "published"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "published")
        self.assertIsNotNone(r.json()["published_at"])

    def test_republish_keeps_original_time(self):
        """发布时间不是更新时间:再发一次不该把它顶到最新。"""
        post = self.new_post("草稿")
        first = self.client.patch(
            f"/api/blog/{post['id']}", json={"status": "published"}
        ).json()["published_at"]
        again = self.client.patch(
            f"/api/blog/{post['id']}", json={"status": "published"}
        ).json()["published_at"]
        self.assertEqual(first, again)

    def test_editing_published_post_keeps_published_at(self):
        post = self.new_post("已发布", status="published")
        got = self.client.patch(
            f"/api/blog/{post['id']}", json={"title": "改个错别字"}
        ).json()
        self.assertEqual(got["published_at"], post["published_at"])

    def test_cannot_go_back_to_draft(self):
        post = self.new_post("已发布", status="published")
        r = self.client.patch(f"/api/blog/{post['id']}", json={"status": "draft"})
        self.assertEqual(r.status_code, 422)
        # 挡住之后不能留下半截改动
        self.assertEqual(
            self.client.get(f"/api/blog/{post['id']}").json()["status"], "published"
        )

    def test_draft_stays_draft_when_editing(self):
        post = self.new_post("草稿")
        got = self.client.patch(f"/api/blog/{post['id']}", json={"title": "改"}).json()
        self.assertEqual(got["status"], "draft")
        self.assertIsNone(got["published_at"])


class DraftIsNotContentTest(BlogTestCase):
    """草稿不进汇总、不能被评论 —— 两处消费点都读 content_types.published_field。"""

    def test_summary_counts_only_published(self):
        self.new_post("草稿一")
        self.new_post("草稿二")
        self.new_post("发了", status="published")

        summary = {item["key"]: item for item in self.client.get("/api/summary").json()}
        self.assertIn("blog", summary)
        self.assertEqual(summary["blog"]["name"], "博客")
        self.assertEqual(summary["blog"]["count"], 1)

    def test_comment_target_rejects_draft(self):
        """不挡的话,target_type=blog&target_id=<猜> 能问出「有这么一篇」。"""
        post = self.new_post("草稿")
        r = self.client.get(
            "/api/comments", params={"target_type": "blog", "target_id": post["id"]}
        )
        self.assertEqual(r.status_code, 404)
        r = self.client.post(
            "/api/comments",
            json={"target_type": "blog", "target_id": post["id"], "body": "hi"},
        )
        self.assertEqual(r.status_code, 404)

    def test_comment_target_accepts_published_post(self):
        post = self.new_post("发了", status="published")
        self.new_comment("blog", post["id"], "第一篇评论")
        rows = self.client.get(
            "/api/comments", params={"target_type": "blog", "target_id": post["id"]}
        ).json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["body"], "第一篇评论")

    def test_blog_is_registered_with_published_field(self):
        """清单里没登记 published_field 的话,上面两条会静默失效。"""
        self.assertEqual(BY_KEY["blog"].published_field, "published_at")
        self.assertIsNone(BY_KEY["product"].published_field)


class BlogActivityTest(BlogTestCase):
    """动态规则:草稿一条都不记,发布记 create,已发布的编辑/删除记 update/delete。"""

    def test_draft_create_not_recorded(self):
        self.new_post("悄悄写的草稿")
        self.assertEqual(self._events(), [])

    def test_draft_edit_not_recorded(self):
        post = self.new_post("草稿")
        self.client.patch(f"/api/blog/{post['id']}", json={"title": "改了"})
        self.assertEqual(self._events(), [])

    def test_draft_delete_not_recorded(self):
        post = self.new_post("草稿")
        self.client.delete(f"/api/blog/{post['id']}")
        self.assertEqual(self._events(), [])

    def test_publish_records_create(self):
        post = self.new_post("草稿")
        self.client.patch(f"/api/blog/{post['id']}", json={"status": "published"})
        events = self._events()
        self.assertEqual(len(events), 1, events)
        self.assertEqual(events[0]["action"], "create")
        self.assertEqual(events[0]["content_type"], "blog")
        self.assertEqual(events[0]["content_type_name"], "博客")
        self.assertEqual(events[0]["title"], "草稿")
        self.assertEqual(events[0]["url"], f"/blog/{post['id']}")

    def test_create_published_directly_records_create(self):
        self.new_post("直接发布", status="published")
        self.assertEqual(self._events()[0]["action"], "create")

    def test_edit_published_records_update(self):
        post = self.new_post("已发布", status="published")
        self.client.patch(f"/api/blog/{post['id']}", json={"title": "改过"})
        events = self._events()
        self.assertEqual(events[0]["action"], "update")
        self.assertEqual(events[0]["title"], "改过")

    def test_delete_published_records_delete_with_null_url(self):
        post = self.new_post("已发布", status="published")
        self.client.delete(f"/api/blog/{post['id']}")
        events = self._events()
        self.assertEqual(events[0]["action"], "delete")
        self.assertEqual(events[0]["title"], "已发布")
        self.assertIsNone(events[0]["url"])


class BlogTagTest(BlogTestCase):
    def test_create_list_rename_delete(self):
        tag = self.new_tag("技术")
        self.assertEqual(self.client.get("/api/blog/tags").json()[0]["name"], "技术")

        r = self.client.patch(f"/api/blog/tags/{tag['id']}", json={"name": "技术笔记"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["name"], "技术笔记")

        self.assertEqual(self.client.delete(f"/api/blog/tags/{tag['id']}").status_code, 204)
        self.assertEqual(self.client.get("/api/blog/tags").json(), [])

    def test_tags_route_is_not_swallowed_by_post_id(self):
        """`/blog/tags` 排在 `/blog/{post_id}` 后面的话,这里会变成 422。"""
        self.assertEqual(self.client.get("/api/blog/tags").status_code, 200)

    def test_duplicate_name_is_409(self):
        self.new_tag("技术")
        self.assertEqual(
            self.client.post("/api/blog/tags", json={"name": "技术"}).status_code, 409
        )

    def test_tag_ids_replace_and_clear(self):
        a = self.new_tag("A")
        b = self.new_tag("B")
        post = self.new_post("带标签", tag_ids=[a["id"], b["id"]])
        self.assertEqual([t["name"] for t in post["tags"]], ["A", "B"])

        got = self.client.patch(
            f"/api/blog/{post['id']}", json={"tag_ids": [b["id"]]}
        ).json()
        self.assertEqual([t["name"] for t in got["tags"]], ["B"])

        # 缺席 = 不动
        got = self.client.patch(f"/api/blog/{post['id']}", json={"title": "改"}).json()
        self.assertEqual([t["name"] for t in got["tags"]], ["B"])

        # [] = 清空
        got = self.client.patch(f"/api/blog/{post['id']}", json={"tag_ids": []}).json()
        self.assertEqual(got["tags"], [])

    def test_unknown_tag_id_is_400(self):
        r = self.client.post("/api/blog", json={"title": "x", "tag_ids": [99999]})
        self.assertEqual(r.status_code, 400)

    def test_filter_by_tag(self):
        tag = self.new_tag("技术")
        tagged = self.new_post("打了标", status="published", tag_ids=[tag["id"]])
        self.new_post("没打标", status="published")

        rows = self._list(tag_id=tag["id"])
        self.assertEqual([p["id"] for p in rows], [tagged["id"]])

    def test_filter_by_tag_hides_others_draft(self):
        """按标签筛也不能绕过草稿可见性 —— 两个条件是与的关系。"""
        tag = self.new_tag("技术")
        self.new_post("管理员的草稿", tag_ids=[tag["id"]])
        self.assertEqual(self._list(client=self.emp, tag_id=tag["id"]), [])

    def test_deleting_tag_keeps_post(self):
        tag = self.new_tag("技术")
        post = self.new_post("有条目", status="published", tag_ids=[tag["id"]])
        self.client.delete(f"/api/blog/tags/{tag['id']}")
        got = self.client.get(f"/api/blog/{post['id']}").json()
        self.assertEqual(got["tags"], [])
        self.assertEqual(got["title"], "有条目")


class PostLikeTest(BlogTestCase):
    def test_like_and_unlike(self):
        post = self.new_post("点赞对象", status="published")

        self.assertEqual(self.client.post(f"/api/blog/{post['id']}/like").status_code, 204)
        got = self.client.get(f"/api/blog/{post['id']}").json()
        self.assertEqual(got["like_count"], 1)
        self.assertTrue(got["liked_by_me"])

        other_view = self.emp.get(f"/api/blog/{post['id']}").json()
        self.assertEqual(other_view["like_count"], 1)
        self.assertFalse(other_view["liked_by_me"])  # 赞是别人点的,不是我点的

        self.assertEqual(self.client.delete(f"/api/blog/{post['id']}/like").status_code, 204)
        self.assertEqual(self.client.get(f"/api/blog/{post['id']}").json()["like_count"], 0)

    def test_like_is_idempotent(self):
        post = self.new_post("点赞对象", status="published")
        self.client.post(f"/api/blog/{post['id']}/like")
        self.assertEqual(self.client.post(f"/api/blog/{post['id']}/like").status_code, 204)
        self.assertEqual(self.client.get(f"/api/blog/{post['id']}").json()["like_count"], 1)

    def test_unlike_is_idempotent(self):
        post = self.new_post("点赞对象", status="published")
        self.assertEqual(self.client.delete(f"/api/blog/{post['id']}/like").status_code, 204)

    def test_second_user_like_counts(self):
        post = self.new_post("点赞对象", status="published")
        self.client.post(f"/api/blog/{post['id']}/like")
        self.emp.post(f"/api/blog/{post['id']}/like")
        self.assertEqual(self.client.get(f"/api/blog/{post['id']}").json()["like_count"], 2)

    def test_cannot_like_invisible_draft(self):
        post = self.new_post("管理员的草稿")
        self.assertEqual(self.emp.post(f"/api/blog/{post['id']}/like").status_code, 404)


class BlogCleanupTest(BlogTestCase):
    """多态指针的账单:删帖子要连带清掉它的评论与点赞。"""

    def test_delete_post_removes_comments_and_likes(self):
        post = self.new_post("带评论的帖子", status="published")
        comment = self.new_comment("blog", post["id"], "一条点评")
        self.client.post(f"/api/comments/{comment['id']}/like")
        self.client.post(f"/api/blog/{post['id']}/like")

        self.client.delete(f"/api/blog/{post['id']}")

        with SessionLocal() as db:
            self.assertEqual(
                db.query(models.Comment)
                .filter(models.Comment.target_type == "blog")
                .count(),
                0,
            )
            self.assertEqual(db.query(models.CommentLike).count(), 0)
            self.assertEqual(db.query(models.PostLike).count(), 0)


if __name__ == "__main__":
    unittest.main()
