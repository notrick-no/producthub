"""内容类型清单(第四版):全站唯一一份「有哪些内容类型」。

首页那三件事都要这份清单:
  1. 写事件时知道 content_type 填什么、标题从对象哪个字段取(见 crud.record_event);
  2. 「项目汇总」要按类型遍历计数(见 routers/home.py);
  3. 「最近动态」要按类型把条目拼成链接。

如果这三处各写一份,以后加会议/博客必然漏掉一处 —— 而且**不会报错**,
只会静默少一张卡片或少一条链接。所以清单只有这一份。

这里只存事实,不存逻辑。一旦开始长出「如果这个类型有分类关联就……」这类分支,
就是抽象过度,该退回去。
"""
from dataclasses import dataclass

from .models import Product, Requirement


@dataclass(frozen=True)
class ContentType:
    key: str  # 存进 activity_events.content_type
    name: str  # 中文名:汇总卡片标题、动态条目的类型标签
    model: type  # ORM 模型,汇总计数用
    path: str  # 前端路由前缀,动态条目的链接由它拼
    title_field: str  # 对象上取「标题」的字段名,写事件时存成快照
    enabled: bool = True  # False = 本版还没做,首页不显示它


# 顺序即首页汇总卡片的顺序。会议 / 博客以后加在这里,前端一行都不用改。
CONTENT_TYPES = (
    ContentType("product", "产品", Product, "/products", "name"),
    ContentType("requirement", "需求", Requirement, "/requirements", "description"),
)

BY_KEY = {ct.key: ct for ct in CONTENT_TYPES}
BY_MODEL = {ct.model: ct for ct in CONTENT_TYPES}


def content_type_of(obj) -> ContentType:
    """按 ORM 对象查出它属于哪一类内容 —— 写事件时不用调用方再手传一遍类型。

    查不到会 KeyError(500),这是故意的:某个模型没登记进 CONTENT_TYPES 却要记动态,
    宁可直接炸,也别静默记成别的类型、或者干脆少记一条 —— 后者更难查。
    tests/test_activity.py 覆盖了每个类型,漏登记过不了测试。
    """
    return BY_MODEL[type(obj)]


def title_of(obj, content_type: ContentType) -> str:
    """取对象的标题,用于事件快照。

    标题字段为空时给个兜底文案,免得动态里出现「新建了「」」这种空壳。
    """
    value = getattr(obj, content_type.title_field, None)
    text = str(value).strip() if value is not None else ""
    return text[:255] or "(无标题)"
