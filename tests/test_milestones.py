"""产品发展历程(ProductMilestone)接口测试:随产品的整体替换语义。"""

from sqlalchemy import text

from app.db import SessionLocal
from tests.base import ApiTestCase


def _milestones(product: dict) -> list[dict]:
    return product["milestones"]


def _count_milestone_rows() -> int:
    with SessionLocal() as db:
        return db.execute(text("SELECT count(*) FROM product_milestones")).scalar_one()


class MilestoneCreateTest(ApiTestCase):
    def test_create_sorts_by_date_asc(self):
        body = self.new_product(
            "Notion",
            milestones=[
                {"date": "2025-02", "title": "开始收费"},
                {"date": "2024-03", "title": "产品成立"},
                {"date": "2024-07", "title": "上线 MVP", "note": "公开测试"},
            ],
        )
        got = [m["title"] for m in _milestones(body)]
        self.assertEqual(got, ["产品成立", "上线 MVP", "开始收费"])
        # 只给年月 → 自动落到当月 1 号
        self.assertEqual(_milestones(body)[0]["date"], "2024-03-01")
        # note 透传
        self.assertEqual(_milestones(body)[1]["note"], "公开测试")
        self.assertIsNone(_milestones(body)[2]["note"])

    def test_create_default_empty(self):
        body = self.new_product("Notion")
        self.assertEqual(_milestones(body), [])

    def test_month_expansion_and_full_day_kept(self):
        body = self.new_product(
            "x",
            milestones=[
                {"date": "2024-3", "title": "单月补零"},
                {"date": "2024-03-15", "title": "精确到日"},
            ],
        )
        got = {m["title"]: m["date"] for m in _milestones(body)}
        self.assertEqual(got["单月补零"], "2024-03-01")
        self.assertEqual(got["精确到日"], "2024-03-15")

    def test_invalid_milestone_rejected(self):
        for bad in [
            {"date": "2024-13", "title": "x"},  # 越界月份
            {"date": "随便", "title": "x"},  # 非日期
            {"date": "2024-03", "title": "   "},  # 空事件名
            {"date": "", "title": "x"},  # 空日期
        ]:
            r = self.client.post("/api/products", json={"name": "x", "milestones": [bad]})
            self.assertEqual(r.status_code, 422, f"{bad!r} 应被拒")


class MilestoneUpdateTest(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.pid = self.new_product("Notion")["id"]

    def test_patch_replaces_all(self):
        self.client.patch(
            f"/api/products/{self.pid}",
            json={
                "milestones": [
                    {"date": "2025-08", "title": "推出 AI 功能"},
                    {"date": "2026-03", "title": "月访问量 500K"},
                ]
            },
        )
        rows = _milestones(
            self.client.get(f"/api/products/{self.pid}").json()
        )
        self.assertEqual([m["title"] for m in rows], ["推出 AI 功能", "月访问量 500K"])

    def test_patch_absent_keeps_milestones(self):
        self.client.patch(
            f"/api/products/{self.pid}",
            json={"milestones": [{"date": "2024-03", "title": "成立"}]},
        )
        self.client.patch(f"/api/products/{self.pid}", json={"name": "改名"})
        rows = _milestones(self.client.get(f"/api/products/{self.pid}").json())
        self.assertEqual([m["title"] for m in rows], ["成立"])

    def test_patch_empty_clears(self):
        self.client.patch(
            f"/api/products/{self.pid}",
            json={"milestones": [{"date": "2024-03", "title": "成立"}]},
        )
        r = self.client.patch(f"/api/products/{self.pid}", json={"milestones": []})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(_milestones(r.json()), [])

    def test_patch_invalid_rolls_back_everything(self):
        self.client.patch(
            f"/api/products/{self.pid}",
            json={"milestones": [{"date": "2024-03", "title": "成立"}]},
        )
        r = self.client.patch(
            f"/api/products/{self.pid}",
            json={"name": "会改吗", "milestones": [{"date": "2024-13", "title": "x"}]},
        )
        self.assertEqual(r.status_code, 422)
        detail = self.client.get(f"/api/products/{self.pid}").json()
        self.assertEqual(detail["name"], "Notion")  # 标量也没改
        self.assertEqual([m["title"] for m in detail["milestones"]], ["成立"])


class MilestoneCascadeTest(ApiTestCase):
    def test_delete_product_cascades_milestones(self):
        pid = self.new_product(
            "Notion", milestones=[{"date": "2024-03", "title": "成立"}]
        )["id"]
        self.assertEqual(_count_milestone_rows(), 1)
        self.client.delete(f"/api/products/{pid}")
        self.assertEqual(_count_milestone_rows(), 0)

    def test_list_includes_milestones(self):
        pid = self.new_product(
            "A", milestones=[{"date": "2025-01", "title": "事件"}]
        )["id"]
        rows = self.client.get("/api/products").json()
        self.assertEqual(rows[0]["id"], pid)
        self.assertEqual(_milestones(rows[0])[0]["title"], "事件")
