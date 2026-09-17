"""权限谓词(app/permissions.py)。

谓词是纯函数,所以这里不碰数据库 —— 用真的 User / Comment 模型在内存里造对象,
**不要 Mock**:这个文件的全部价值就在于「字段名和真实模型对得上」,用假对象测等于没测。
"""
import unittest

from app import models, permissions


def _user(uid: int, role: str) -> models.User:
    return models.User(id=uid, email=f"u{uid}@test.local", name=f"用户{uid}", role=role)


def _comment(author_id: int | None) -> models.Comment:
    return models.Comment(
        target_type="product",
        target_id=1,
        author_id=author_id,
        author_name="某人",
        body="x",
    )


def _conversation(created_by: int) -> models.AiConversation:
    return models.AiConversation(title="会话", created_by=created_by)


class CanDeleteCommentTest(unittest.TestCase):
    """一行的三个人:作者本人、别的员工、管理员。"""

    def test_author_can_delete_own_comment(self):
        self.assertTrue(
            permissions.can_delete_comment(_user(7, "employee"), _comment(7))
        )

    def test_other_employee_cannot_delete(self):
        self.assertFalse(
            permissions.can_delete_comment(_user(8, "employee"), _comment(7))
        )

    def test_admin_can_delete_anyones_comment(self):
        self.assertTrue(permissions.can_delete_comment(_user(9, "admin"), _comment(7)))

    def test_admin_can_delete_orphan_comment(self):
        """账号被删(SET NULL)的评论只有管理员能删 —— 没有作者可供普通员工认领。"""
        self.assertTrue(
            permissions.can_delete_comment(_user(9, "admin"), _comment(None))
        )

    def test_employee_cannot_claim_orphan_comment(self):
        """author_id 为空时不能因为「我也没有」就判成自己的。"""
        self.assertFalse(
            permissions.can_delete_comment(_user(1, "employee"), _comment(None))
        )


class CanViewAiConversationTest(unittest.TestCase):
    """AI 会话**只本人可见** —— 这一条与 can_view_post 故意相反。

    博客是写给全公司看的,草稿只是还没写完;AI 会话是一个人问问题的方式,
    没有任何人是「为了给别人看」才去问的。所以这里管理员**必须**是 False。
    """

    def test_creator_can_view(self):
        self.assertTrue(
            permissions.can_view_ai_conversation(_user(7, "employee"), _conversation(7))
        )

    def test_other_user_cannot_view(self):
        self.assertFalse(
            permissions.can_view_ai_conversation(_user(8, "employee"), _conversation(7))
        )

    def test_admin_cannot_view_someone_elses(self):
        """最容易写错的一条:顺手写成 `or user.role == "admin"` 就没了。"""
        self.assertFalse(
            permissions.can_view_ai_conversation(_user(9, "admin"), _conversation(7))
        )

    def test_admin_can_view_own(self):
        self.assertTrue(
            permissions.can_view_ai_conversation(_user(9, "admin"), _conversation(9))
        )


class CanManageAiSettingsTest(unittest.TestCase):
    """限额是管理员管的旋钮 —— 与「看别人的对话」是两件事。"""

    def test_admin_can_manage(self):
        self.assertTrue(permissions.can_manage_ai_settings(_user(1, "admin")))

    def test_employee_cannot_manage(self):
        self.assertFalse(permissions.can_manage_ai_settings(_user(2, "employee")))


if __name__ == "__main__":
    unittest.main()
