"""分级定价(ProductPriceTier)接口测试:随产品的整体替换语义。"""

from sqlalchemy import text

from app.db import SessionLocal
from tests.base import ApiTestCase


def _tiers(product: dict) -> list[dict]:
    return product["price_tiers"]


def _count_price_tier_rows() -> int:
    with SessionLocal() as db:
        return db.execute(text("SELECT count(*) FROM product_price_tiers")).scalar_one()


class PriceTierCreateTest(ApiTestCase):
    def test_create_keeps_input_order_and_fields(self):
        body = self.new_product(
            "Notion",
            price_tiers=[
                {"name": "Free", "amount": 0, "cycle": "一次性", "note": "个人免费"},
                {"name": "Pro", "amount": 120, "cycle": "年", "note": "含高级功能"},
                {"name": "企业版", "amount": None, "cycle": None, "note": "按需定制"},
            ],
        )
        got = _tiers(body)
        self.assertEqual([t["name"] for t in got], ["Free", "Pro", "企业版"])
        # 字段逐档透传(金额以数字返回)
        self.assertEqual(got[0]["amount"], 0)
        self.assertEqual(got[1]["amount"], 120)
        self.assertEqual(got[1]["cycle"], "年")
        self.assertEqual(got[1]["note"], "含高级功能")
        # 面议档:amount / cycle 可空
        self.assertIsNone(got[2]["amount"])
        self.assertIsNone(got[2]["cycle"])

    def test_create_default_empty(self):
        body = self.new_product("Notion")
        self.assertEqual(_tiers(body), [])

    def test_partial_rows_allowed(self):
        # 只填档名 / 只填金额等半截行都允许(档名可选)
        body = self.new_product(
            "Notion",
            price_tiers=[
                {"name": "只有档名"},
                {"amount": 9.9, "cycle": "月"},
                {"note": "只有备注"},
                {},
            ],
        )
        got = _tiers(body)
        self.assertEqual(len(got), 4)
        self.assertEqual(got[1]["name"], None)
        self.assertEqual(got[1]["amount"], 9.9)

    def test_blank_strings_normalized_to_null(self):
        body = self.new_product(
            "Notion",
            price_tiers=[{"name": "  ", "amount": 5, "cycle": "", "note": ""}],
        )
        t = _tiers(body)[0]
        self.assertIsNone(t["name"])
        self.assertIsNone(t["cycle"])
        self.assertIsNone(t["note"])

    def test_invalid_tier_rejected(self):
        for bad in [
            {"name": "x", "amount": -1},  # 负数金额
            {"amount": "不是数字"},
            {"name": "档" * 101},  # 档名超长
            {"name": "x", "cycle": "期" * 21},  # 周期超长
        ]:
            r = self.client.post(
                "/api/products", json={"name": "x", "price_tiers": [bad]}
            )
            self.assertEqual(r.status_code, 422, f"{bad!r} 应被拒")


class PriceTierUpdateTest(ApiTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.pid = self.new_product("Notion")["id"]

    def test_patch_replaces_all(self):
        self.client.patch(
            f"/api/products/{self.pid}",
            json={
                "price_tiers": [
                    {"name": "Free", "amount": 0},
                    {"name": "Pro", "amount": 288, "cycle": "年"},
                ]
            },
        )
        rows = _tiers(self.client.get(f"/api/products/{self.pid}").json())
        self.assertEqual([t["name"] for t in rows], ["Free", "Pro"])
        self.assertEqual(rows[1]["amount"], 288)

    def test_patch_absent_keeps_tiers(self):
        self.client.patch(
            f"/api/products/{self.pid}",
            json={"price_tiers": [{"name": "Pro", "amount": 100}]},
        )
        self.client.patch(f"/api/products/{self.pid}", json={"name": "改名"})
        rows = _tiers(self.client.get(f"/api/products/{self.pid}").json())
        self.assertEqual([t["name"] for t in rows], ["Pro"])

    def test_patch_empty_clears(self):
        self.client.patch(
            f"/api/products/{self.pid}",
            json={"price_tiers": [{"name": "Pro", "amount": 100}]},
        )
        r = self.client.patch(f"/api/products/{self.pid}", json={"price_tiers": []})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(_tiers(r.json()), [])

    def test_patch_invalid_rolls_back_everything(self):
        self.client.patch(
            f"/api/products/{self.pid}",
            json={"price_tiers": [{"name": "Pro", "amount": 100}]},
        )
        r = self.client.patch(
            f"/api/products/{self.pid}",
            json={"name": "会改吗", "price_tiers": [{"amount": -3}]},
        )
        self.assertEqual(r.status_code, 422)
        detail = self.client.get(f"/api/products/{self.pid}").json()
        self.assertEqual(detail["name"], "Notion")  # 标量也没改
        self.assertEqual([t["name"] for t in detail["price_tiers"]], ["Pro"])


class PriceTierCascadeTest(ApiTestCase):
    def test_delete_product_cascades_price_tiers(self):
        pid = self.new_product(
            "Notion", price_tiers=[{"name": "Pro", "amount": 100}]
        )["id"]
        self.assertEqual(_count_price_tier_rows(), 1)
        self.client.delete(f"/api/products/{pid}")
        self.assertEqual(_count_price_tier_rows(), 0)

    def test_list_includes_price_tiers(self):
        pid = self.new_product(
            "A", price_tiers=[{"name": "Free", "amount": 0}]
        )["id"]
        rows = self.client.get("/api/products").json()
        self.assertEqual(rows[0]["id"], pid)
        self.assertEqual(_tiers(rows[0])[0]["name"], "Free")

    def test_detail_sorts_by_id_keeps_order(self):
        pid = self.new_product(
            "Notion",
            price_tiers=[
                {"name": "档一"},
                {"name": "档二"},
                {"name": "档三"},
            ],
        )["id"]
        got = _tiers(self.client.get(f"/api/products/{pid}").json())
        self.assertEqual([t["name"] for t in got], ["档一", "档二", "档三"])
