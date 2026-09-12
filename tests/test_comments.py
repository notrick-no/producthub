"""评论与点赞(第五版 5B)。

三类要紧的事:
  1. **多态指针的账单** —— 删产品/需求必须连带清掉评论、点赞、以及图片文件。
     数据库不知道 target_id 指谁,漏了不会报错,只会留一堆看不见的行。
  2. **墓碑** —— 删一条顶层评论不能让别人写的回复跟着消失。
  3. **归属校验** —— 删别人的评论必须 403,而且前端藏没藏按钮不算数。
"""
import unittest

from fastapi.testclient import TestClient

from tests.base import ApiTestCase, SessionLocal
from app import models
from app.storage import UPLOAD_DIR


class CommentCrudTest(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = self.new_product("被评论的产品")

    def _list(self, target_type: str = "product", target_id: int | None = None):
        r = self.client.get(
            "/api/comments",
            params={
                "target_type": target_type,
                "target_id": target_id if target_id is not None else self.product["id"],
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_create_and_list(self):
        self.new_comment("product", self.product["id"], "第一条")
        rows = self._list()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["body"], "第一条")
        self.assertEqual(rows[0]["author_name"], "测试管理员")
        self.assertEqual(rows[0]["like_count"], 0)
        self.assertFalse(rows[0]["liked_by_me"])
        self.assertIsNone(rows[0]["deleted_at"])
        self.assertEqual(rows[0]["replies"], [])

    def test_unknown_target_type_is_404(self):
        r = self.client.get(
            "/api/comments", params={"target_type": "meeting", "target_id": 1}
        )
        self.assertEqual(r.status_code, 404)

    def test_missing_target_is_404(self):
        r = self.client.get(
            "/api/comments", params={"target_type": "product", "target_id": 99999}
        )
        self.assertEqual(r.status_code, 404)

    def test_cannot_comment_on_missing_target(self):
        r = self.client.post(
            "/api/comments",
            json={"target_type": "product", "target_id": 99999, "body": "hi"},
        )
        self.assertEqual(r.status_code, 404)

    def test_empty_body_is_rejected(self):
        r = self.client.post(
            "/api/comments",
            json={"target_type": "product", "target_id": self.product["id"], "body": "   "},
        )
        self.assertEqual(r.status_code, 422)

    def test_requirements_use_the_same_endpoint(self):
        req = self.new_requirement("被评论的需求")
        self.new_comment("requirement", req["id"], "需求上的评论")
        rows = self._list("requirement", req["id"])
        self.assertEqual([c["body"] for c in rows], ["需求上的评论"])
        # 同一个产品的评论列表不受影响
        self.assertEqual(self._list(), [])

    def test_comments_are_scoped_to_one_target(self):
        other = self.new_product("另一个产品")
        self.new_comment("product", self.product["id"], "A")
        self.new_comment("product", other["id"], "B")
        self.assertEqual([c["body"] for c in self._list()], ["A"])
        self.assertEqual([c["body"] for c in self._list(target_id=other["id"])], ["B"])


class CommentReplyTest(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = self.new_product("带讨论的产品")

    def test_reply_nests_under_parent(self):
        root = self.new_comment("product", self.product["id"], "顶层")
        self.new_comment("product", self.product["id"], "回复", parent_id=root["id"])
        rows = self.client.get(
            "/api/comments",
            params={"target_type": "product", "target_id": self.product["id"]},
        ).json()
        self.assertEqual(len(rows), 1, "回复不该同时出现在顶层")
        self.assertEqual([r["body"] for r in rows[0]["replies"]], ["回复"])

    def test_reply_to_reply_is_rejected(self):
        """只允许一层。"""
        root = self.new_comment("product", self.product["id"], "顶层")
        reply = self.new_comment(
            "product", self.product["id"], "回复", parent_id=root["id"]
        )
        r = self.client.post(
            "/api/comments",
            json={
                "target_type": "product",
                "target_id": self.product["id"],
                "body": "回复的回复",
                "parent_id": reply["id"],
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_reply_to_missing_parent_is_422(self):
        r = self.client.post(
            "/api/comments",
            json={
                "target_type": "product",
                "target_id": self.product["id"],
                "body": "x",
                "parent_id": 99999,
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_reply_to_another_targets_comment_is_rejected(self):
        """不校验的话,回复会挂到别的对象的评论下,读的时候按 target 过滤就再也看不见。"""
        other = self.new_product("另一个产品")
        root = self.new_comment("product", other["id"], "别处的顶层")
        r = self.client.post(
            "/api/comments",
            json={
                "target_type": "product",
                "target_id": self.product["id"],
                "body": "跨对象回复",
                "parent_id": root["id"],
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_reply_to_requirement_comment_from_product_is_rejected(self):
        req = self.new_requirement("需求")
        root = self.new_comment("requirement", req["id"], "需求上的顶层")
        r = self.client.post(
            "/api/comments",
            json={
                "target_type": "product",
                "target_id": self.product["id"],
                "body": "跨类型回复",
                "parent_id": root["id"],
            },
        )
        self.assertEqual(r.status_code, 422)


class CommentDeleteTest(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = self.new_product("产品")
        self.other = self._add_user(
            email="other@test.local", name="李四", password="OtherPass2026"
        )
        self.other_client = self.login_client("other@test.local", "OtherPass2026")

    def _one(self, cid: int) -> dict:
        rows = self.client.get(
            "/api/comments",
            params={"target_type": "product", "target_id": self.product["id"]},
        ).json()
        for root in rows:
            if root["id"] == cid:
                return root
            for reply in root["replies"]:
                if reply["id"] == cid:
                    return reply
        self.fail(f"评论 {cid} 不在列表里")

    def test_author_can_delete_own_comment(self):
        comment = self.new_comment("product", self.product["id"], "我的评论")
        r = self.client.delete(f"/api/comments/{comment['id']}")
        self.assertEqual(r.status_code, 204, r.text)
        row = self._one(comment["id"])
        self.assertEqual(row["body"], "")
        self.assertIsNotNone(row["deleted_at"])

    def test_employee_cannot_delete_someone_elses_comment(self):
        comment = self.new_comment("product", self.product["id"], "管理员的评论")
        r = self.other_client.delete(f"/api/comments/{comment['id']}")
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self._one(comment["id"])["body"], "管理员的评论")

    def test_admin_can_delete_anyones_comment(self):
        comment = self.new_comment(
            "product", self.product["id"], "李四的评论", client=self.other_client
        )
        r = self.client.delete(f"/api/comments/{comment['id']}")
        self.assertEqual(r.status_code, 204, r.text)
        self.assertIsNotNone(self._one(comment["id"])["deleted_at"])

    def test_author_can_delete_own_reply(self):
        root = self.new_comment("product", self.product["id"], "顶层")
        reply = self.new_comment(
            "product", self.product["id"], "我的回复", client=self.other_client,
            parent_id=root["id"],
        )
        r = self.other_client.delete(f"/api/comments/{reply['id']}")
        self.assertEqual(r.status_code, 204, r.text)

    def test_tombstone_keeps_other_peoples_replies(self):
        """墓碑的意义所在:删顶层评论不能连带删掉别人写的回复。"""
        root = self.new_comment("product", self.product["id"], "顶层的")
        self.new_comment(
            "product", self.product["id"], "李四的回复", client=self.other_client,
            parent_id=root["id"],
        )
        self.client.delete(f"/api/comments/{root['id']}")

        rows = self.client.get(
            "/api/comments",
            params={"target_type": "product", "target_id": self.product["id"]},
        ).json()
        self.assertEqual(len(rows), 1, "墓碑评论本身要留着,否则回复会变成孤儿")
        self.assertEqual(rows[0]["body"], "")
        self.assertIsNotNone(rows[0]["deleted_at"])
        self.assertEqual([r["body"] for r in rows[0]["replies"]], ["李四的回复"])

    def test_delete_is_idempotent(self):
        comment = self.new_comment("product", self.product["id"], "x")
        self.assertEqual(self.client.delete(f"/api/comments/{comment['id']}").status_code, 204)
        self.assertEqual(self.client.delete(f"/api/comments/{comment['id']}").status_code, 204)

    def test_cannot_reply_to_a_deleted_comment(self):
        root = self.new_comment("product", self.product["id"], "顶层")
        self.client.delete(f"/api/comments/{root['id']}")
        r = self.client.post(
            "/api/comments",
            json={
                "target_type": "product",
                "target_id": self.product["id"],
                "body": "回复墓碑",
                "parent_id": root["id"],
            },
        )
        self.assertEqual(r.status_code, 422)

    def test_delete_missing_comment_is_404(self):
        self.assertEqual(self.client.delete("/api/comments/99999").status_code, 404)

    def test_requires_login(self):
        comment = self.new_comment("product", self.product["id"], "x")
        anon = TestClient(self.client.app)
        self.assertEqual(
            anon.get(
                "/api/comments",
                params={"target_type": "product", "target_id": self.product["id"]},
            ).status_code,
            401,
        )
        self.assertEqual(anon.delete(f"/api/comments/{comment['id']}").status_code, 401)


class CommentLikeTest(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.product = self.new_product("产品")
        self.comment = self.new_comment("product", self.product["id"], "被赞的评论")

    def _row(self) -> dict:
        return self.client.get(
            "/api/comments",
            params={"target_type": "product", "target_id": self.product["id"]},
        ).json()[0]

    def test_like_counts_and_flags_me(self):
        r = self.client.post(f"/api/comments/{self.comment['id']}/like")
        self.assertEqual(r.status_code, 204, r.text)
        row = self._row()
        self.assertEqual(row["like_count"], 1)
        self.assertTrue(row["liked_by_me"])

    def test_liking_twice_is_idempotent(self):
        """复合主键挡住重复行;第二次不该 409,更不该多算一个赞。"""
        self.client.post(f"/api/comments/{self.comment['id']}/like")
        r = self.client.post(f"/api/comments/{self.comment['id']}/like")
        self.assertEqual(r.status_code, 204, r.text)
        self.assertEqual(self._row()["like_count"], 1)

    def test_unlike(self):
        self.client.post(f"/api/comments/{self.comment['id']}/like")
        r = self.client.delete(f"/api/comments/{self.comment['id']}/like")
        self.assertEqual(r.status_code, 204, r.text)
        row = self._row()
        self.assertEqual(row["like_count"], 0)
        self.assertFalse(row["liked_by_me"])

    def test_unlike_without_liking_is_idempotent(self):
        r = self.client.delete(f"/api/comments/{self.comment['id']}/like")
        self.assertEqual(r.status_code, 204, r.text)

    def test_like_count_is_per_comment_not_per_thread(self):
        """一条 GROUP BY 取回全部 —— 别人的赞不能算到这条头上。"""
        second = self.new_comment("product", self.product["id"], "第二条")
        self.client.post(f"/api/comments/{self.comment['id']}/like")
        self.client.post(f"/api/comments/{second['id']}/like")
        self.client.post(f"/api/comments/{second['id']}/like")  # 幂等,不该变 2

        rows = self.client.get(
            "/api/comments",
            params={"target_type": "product", "target_id": self.product["id"]},
        ).json()
        counts = {row["id"]: row["like_count"] for row in rows}
        self.assertEqual(counts[self.comment["id"]], 1)
        self.assertEqual(counts[second["id"]], 1)

    def test_likes_on_replies_are_counted(self):
        reply = self.new_comment(
            "product", self.product["id"], "回复", parent_id=self.comment["id"]
        )
        self.client.post(f"/api/comments/{reply['id']}/like")
        row = self._row()
        self.assertEqual(row["like_count"], 0)
        self.assertEqual(row["replies"][0]["like_count"], 1)

    def test_cannot_like_a_deleted_comment(self):
        self.client.delete(f"/api/comments/{self.comment['id']}")
        r = self.client.post(f"/api/comments/{self.comment['id']}/like")
        self.assertEqual(r.status_code, 422)

    def test_like_missing_comment_is_404(self):
        self.assertEqual(self.client.post("/api/comments/99999/like").status_code, 404)


class CommentAuthorNameTest(ApiTestCase):
    def test_renamed_user_shows_current_name(self):
        """评论是活的内容:用户改名后,历史评论上的署名应当跟着改。"""
        self._add_user(email="ren@test.local", name="原名", password="RenPass2026")
        client = self.login_client("ren@test.local", "RenPass2026")
        product = self.new_product("产品")
        self.new_comment("product", product["id"], "x", client=client)

        with SessionLocal() as db:
            user = db.query(models.User).filter_by(email="ren@test.local").one()
            user.name = "新名"
            db.commit()

        rows = self.client.get(
            "/api/comments",
            params={"target_type": "product", "target_id": product["id"]},
        ).json()
        self.assertEqual(rows[0]["author_name"], "新名")

    def test_deleted_user_falls_back_to_snapshot(self):
        """账号被删(author_id → NULL)之后,评论上的署名回落成写入时的名字。"""
        uid = self._add_user(
            email="gone@test.local", name="离职的人", password="GonePass2026"
        )
        client = self.login_client("gone@test.local", "GonePass2026")
        product = self.new_product("产品")
        self.new_comment("product", product["id"], "x", client=client)

        with SessionLocal() as db:
            db.delete(db.get(models.User, uid))
            db.commit()

        rows = self.client.get(
            "/api/comments",
            params={"target_type": "product", "target_id": product["id"]},
        ).json()
        self.assertIsNone(rows[0]["author_id"])
        self.assertEqual(rows[0]["author_name"], "离职的人")


class CommentCleanupTest(ApiTestCase):
    """多态外键的账单:删内容必须自己清评论,数据库不会替我们级联。"""

    def _counts(self, comment_ids: list[int]) -> tuple[int, int]:
        with SessionLocal() as db:
            comments = (
                db.query(models.Comment)
                .filter(models.Comment.id.in_(comment_ids))
                .count()
            )
            likes = (
                db.query(models.CommentLike)
                .filter(models.CommentLike.comment_id.in_(comment_ids))
                .count()
            )
        return comments, likes

    def test_deleting_product_removes_its_comments_and_likes(self):
        product = self.new_product("将被删除的产品")
        other = self.new_product("不该受影响的产品")
        root = self.new_comment("product", product["id"], "顶层")
        reply = self.new_comment(
            "product", product["id"], "回复", parent_id=root["id"]
        )
        self.client.post(f"/api/comments/{root['id']}/like")
        self.client.post(f"/api/comments/{reply['id']}/like")
        keep = self.new_comment("product", other["id"], "别处的评论")

        r = self.client.delete(f"/api/products/{product['id']}")
        self.assertEqual(r.status_code, 204, r.text)

        self.assertEqual(self._counts([root["id"], reply["id"]]), (0, 0))
        self.assertEqual(self._counts([keep["id"]]), (1, 0))

    def test_deleting_requirement_removes_its_comments(self):
        req = self.new_requirement("将被删除的需求")
        root = self.new_comment("requirement", req["id"], "顶层")
        self.new_comment("requirement", req["id"], "回复", parent_id=root["id"])
        self.client.post(f"/api/comments/{root['id']}/like")

        r = self.client.delete(f"/api/requirements/{req['id']}")
        self.assertEqual(r.status_code, 204, r.text)
        self.assertEqual(self._counts([root["id"]]), (0, 0))

    def test_deleting_product_removes_its_image_files(self):
        """第四版遗留的漏文件问题:删产品只删了元数据行,磁盘文件一直留着。"""
        product = self.new_product("带图的产品")
        r = self.client.post(
            f"/api/products/{product['id']}/images",
            files={"file": ("a.png", b"\x89PNG\r\n\x1a\n" + b"x" * 32, "image/png")},
        )
        self.assertEqual(r.status_code, 201, r.text)
        path = r.json()["path"]
        on_disk = UPLOAD_DIR / path.rsplit("/", 1)[-1]
        self.assertTrue(on_disk.exists())

        self.assertEqual(self.client.delete(f"/api/products/{product['id']}").status_code, 204)
        self.assertFalse(on_disk.exists(), "产品删了,图片文件还留在磁盘上")

    def test_deleting_requirement_leaves_products_alone(self):
        """别删错对象:同一个 id 的产品评论不能因为删需求被带走。"""
        product = self.new_product("产品")
        req = self.new_requirement("需求")
        # 两个对象的 id 都是各自表里的 1
        self.assertEqual(product["id"], req["id"])
        keep = self.new_comment("product", product["id"], "产品的评论")
        self.new_comment("requirement", req["id"], "需求的评论")

        self.client.delete(f"/api/requirements/{req['id']}")
        self.assertEqual(self._counts([keep["id"]]), (1, 0))


if __name__ == "__main__":
    unittest.main()
