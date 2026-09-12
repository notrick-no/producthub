"""权限判断(第五版)。

**为什么不建 roles / permissions / role_permissions 三张表**:今天全部权限只由两个轴
决定 —— **角色**(admin / employee)和 **归属**(这条东西是不是你写的)。建三张表放进
去的会是一个只有一行的矩阵,却要让每个判断变成「查表 → 判断」,正是 doc/架构.md 里
说的「抽象之后长出 if/else」。这些具名谓词同样可扩展:加一种能力 = 加一个函数。

**什么时候该换成表**(写在这里免得以后忘):① 需要**按人**授权(而不是按角色),
或 ② 需要**在界面上配置**权限 —— 任一成立时换成表,那时把这些谓词的函数体改成查表即可,
调用点一处都不用动。

谓词只回答「能不能」,不负责报错。调用方照常 raise HTTPException —— 这样同一个谓词
既能用在「守卫」也能用在「过滤」(比如列表里决定哪些行显示删除按钮)。
"""
from .models import BlogPost, Comment, User


def can_delete_comment(user: User, comment: Comment) -> bool:
    """能不能删这条评论:作者本人,或管理员。

    `author_id` 可能为空(账号被删,SET NULL)。那种评论**只有管理员**能删 ——
    没有作者可归属,就不能让任何一个普通员工认领它。
    """
    if user.role == "admin":
        return True
    return comment.author_id is not None and comment.author_id == user.id


def _is_author(user: User, obj) -> bool:
    """这条东西是不是他写的。`author_id` 为空(作者账号被删)时谁都不是。"""
    return obj.author_id is not None and obj.author_id == user.id


def can_view_post(user: User, post: BlogPost) -> bool:
    """能不能看见这篇帖子:**草稿只有作者与管理员看得见**。

    看不见时的正确答复是 **404 而不是 403** —— 403 等于承认「有这么一篇」,
    那本身就是不该漏的信息。所以调用方拿到 False 要照「不存在」处理。
    """
    return post.published_at is not None or user.role == "admin" or _is_author(user, post)


def can_edit_post(user: User, post: BlogPost) -> bool:
    """能不能改 / 删这篇帖子:作者本人,或管理员(与删除评论同一条规矩)。

    **发布状态不在这里管**:「已发布的不能退回草稿」是编辑的字段规则,
    不是「谁能改」的问题,所以它留在 routers/blog.py。
    """
    return user.role == "admin" or _is_author(user, post)
