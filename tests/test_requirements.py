"""需求记录(第四版)CRUD 接口测试。"""
from fastapi.testclient import TestClient

from tests.base import ApiTestCase

# 四组枚举取值 —— 第四版和用户逐条定稿的契约,故意在测试里写死一份。
# 以后要改取值,这里会红,提醒你前端 requirementMeta.ts 也得跟着改。
PRIORITIES = ["高", "中", "低"]
SOURCES = ["用户反馈", "内部提出", "竞品分析", "数据分析"]
PRODUCT_TYPES = ["网站", "移动 App", "小程序", "桌面端", "浏览器插件", "其他"]
STATUSES = ["待评估", "已排期", "进行中", "已完成", "已搁置"]

_ALL_OPTIONAL = (
    "detail",
    "priority",
    "source",
    "product_type",
    "proposed_on",
    "status",
    "estimated_days",
    "due_on",
    "link_url",
    "note",
)


class RequirementCreateTest(ApiTestCase):
    def test_create_minimal(self):
        r = self.client.post("/api/requirements", json={"description": "支持导出 CSV"})
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["description"], "支持导出 CSV")
        self.assertIsNotNone(body["id"])
        self.assertIsNotNone(body["created_at"])
        self.assertIsNotNone(body["updated_at"])
        for field in _ALL_OPTIONAL:
            self.assertIsNone(body[field], field)

    def test_create_full(self):
        body = self.new_requirement(
            "支持导出 CSV",
            detail="详情正文,可以很长。",
            priority="高",
            source="用户反馈",
            product_type="网站",
            proposed_on="2026-09-01",
            status="已排期",
            estimated_days=5,
            due_on="2026-09-20",
            link_url="https://example.com/issues/1",
            note="备注",
        )
        self.assertEqual(body["detail"], "详情正文,可以很长。")
        self.assertEqual(body["priority"], "高")
        self.assertEqual(body["source"], "用户反馈")
        self.assertEqual(body["product_type"], "网站")
        self.assertEqual(body["proposed_on"], "2026-09-01")
        self.assertEqual(body["status"], "已排期")
        self.assertEqual(body["estimated_days"], 5)
        self.assertEqual(body["due_on"], "2026-09-20")
        self.assertEqual(body["link_url"], "https://example.com/issues/1")
        self.assertEqual(body["note"], "备注")

    def test_create_strips_whitespace(self):
        body = self.new_requirement("  支持导出 CSV  ")
        self.assertEqual(body["description"], "支持导出 CSV")

    def test_create_missing_description_rejected(self):
        r = self.client.post("/api/requirements", json={"priority": "高"})
        self.assertEqual(r.status_code, 422)

    def test_create_blank_description_rejected(self):
        r = self.client.post("/api/requirements", json={"description": "   "})
        self.assertEqual(r.status_code, 422)

    def test_create_blank_link_becomes_null(self):
        body = self.new_requirement("带链接的需求", link_url="   ")
        self.assertIsNone(body["link_url"])

    def test_create_accepts_every_enum_value(self):
        """四组枚举的每个取值都要能存能取 —— 锁定第四版定稿的取值表。"""
        for value in PRIORITIES:
            self.assertEqual(self.new_requirement(f"优先级 {value}", priority=value)["priority"], value)
        for value in SOURCES:
            self.assertEqual(self.new_requirement(f"来源 {value}", source=value)["source"], value)
        for value in PRODUCT_TYPES:
            self.assertEqual(
                self.new_requirement(f"类型 {value}", product_type=value)["product_type"], value
            )
        for value in STATUSES:
            self.assertEqual(self.new_requirement(f"状态 {value}", status=value)["status"], value)

    def test_create_invalid_priority_rejected(self):
        r = self.client.post(
            "/api/requirements", json={"description": "x", "priority": "紧急"}
        )
        self.assertEqual(r.status_code, 422)

    def test_create_invalid_status_rejected(self):
        r = self.client.post(
            "/api/requirements", json={"description": "x", "status": "在做"}
        )
        self.assertEqual(r.status_code, 422)

    def test_create_invalid_source_rejected(self):
        r = self.client.post(
            "/api/requirements", json={"description": "x", "source": "老板说的"}
        )
        self.assertEqual(r.status_code, 422)

    def test_create_invalid_product_type_rejected(self):
        r = self.client.post(
            "/api/requirements", json={"description": "x", "product_type": "电视"}
        )
        self.assertEqual(r.status_code, 422)

    def test_create_negative_estimated_days_rejected(self):
        r = self.client.post(
            "/api/requirements", json={"description": "x", "estimated_days": -1}
        )
        self.assertEqual(r.status_code, 422)

    def test_create_zero_estimated_days_allowed(self):
        body = self.new_requirement("零投入", estimated_days=0)
        self.assertEqual(body["estimated_days"], 0)

    def test_create_bad_date_rejected(self):
        r = self.client.post(
            "/api/requirements", json={"description": "x", "proposed_on": "2026/09/01"}
        )
        self.assertEqual(r.status_code, 422)


class RequirementListTest(ApiTestCase):
    def test_empty_list(self):
        r = self.client.get("/api/requirements")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_list_newest_first(self):
        first = self.new_requirement("先提的")["id"]
        second = self.new_requirement("后提的")["id"]
        r = self.client.get("/api/requirements")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["id"] for x in r.json()], [second, first])


class RequirementGetTest(ApiTestCase):
    def test_get(self):
        created = self.new_requirement("支持导出 CSV", priority="高")
        r = self.client.get(f"/api/requirements/{created['id']}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["description"], "支持导出 CSV")
        self.assertEqual(r.json()["priority"], "高")

    def test_get_not_found(self):
        self.assertEqual(self.client.get("/api/requirements/999999").status_code, 404)


class RequirementUpdateTest(ApiTestCase):
    def test_patch_only_updates_sent_fields(self):
        rid = self.new_requirement("原名", detail="原详情", priority="低")["id"]
        r = self.client.patch(f"/api/requirements/{rid}", json={"status": "进行中"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["status"], "进行中")
        self.assertEqual(body["description"], "原名")  # 未传,不应被改动
        self.assertEqual(body["detail"], "原详情")
        self.assertEqual(body["priority"], "低")

    def test_patch_can_clear_optional_field(self):
        rid = self.new_requirement("有条目", note="写点什么")["id"]
        r = self.client.patch(f"/api/requirements/{rid}", json={"note": None})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["note"])

    def test_patch_blank_description_rejected(self):
        rid = self.new_requirement("原名")["id"]
        r = self.client.patch(f"/api/requirements/{rid}", json={"description": "   "})
        self.assertEqual(r.status_code, 422)

    def test_patch_invalid_enum_rejected(self):
        rid = self.new_requirement("原名")["id"]
        r = self.client.patch(f"/api/requirements/{rid}", json={"priority": "特高"})
        self.assertEqual(r.status_code, 422)

    def test_patch_not_found(self):
        r = self.client.patch("/api/requirements/999999", json={"status": "已完成"})
        self.assertEqual(r.status_code, 404)


class RequirementDeleteTest(ApiTestCase):
    def test_delete(self):
        rid = self.new_requirement("要删掉的")["id"]
        self.assertEqual(self.client.delete(f"/api/requirements/{rid}").status_code, 204)
        self.assertEqual(self.client.get("/api/requirements").json(), [])
        self.assertEqual(self.client.get(f"/api/requirements/{rid}").status_code, 404)

    def test_delete_not_found(self):
        self.assertEqual(self.client.delete("/api/requirements/999999").status_code, 404)


class RequirementAuthTest(ApiTestCase):
    def test_anonymous_gets_401(self):
        from app.main import app

        client = TestClient(app)
        self.assertEqual(client.get("/api/requirements").status_code, 401)
        self.assertEqual(
            client.post("/api/requirements", json={"description": "x"}).status_code, 401
        )
