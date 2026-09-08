"""分类 CRUD 接口测试。"""

from tests.base import ApiTestCase


class CategoryCreateTest(ApiTestCase):
    def test_create(self):
        r = self.client.post(
            "/api/categories", json={"name": "AI 工具", "description": "AI 产品"}
        )
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["name"], "AI 工具")
        self.assertEqual(body["description"], "AI 产品")
        self.assertIsNotNone(body["id"])
        self.assertIsNotNone(body["created_at"])

    def test_create_strips_whitespace(self):
        r = self.client.post("/api/categories", json={"name": "  SaaS  "})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["name"], "SaaS")

    def test_create_blank_description_becomes_null(self):
        r = self.client.post("/api/categories", json={"name": "工具", "description": "  "})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertIsNone(r.json()["description"])

    def test_create_blank_name_rejected(self):
        r = self.client.post("/api/categories", json={"name": "   "})
        self.assertEqual(r.status_code, 422)

    def test_create_missing_name_rejected(self):
        r = self.client.post("/api/categories", json={"description": "没有名字"})
        self.assertEqual(r.status_code, 422)

    def test_create_duplicate_name_conflict(self):
        self.new_category("AI 工具")
        r = self.client.post("/api/categories", json={"name": "AI 工具"})
        self.assertEqual(r.status_code, 409)


class CategoryListTest(ApiTestCase):
    def test_empty_list(self):
        r = self.client.get("/api/categories")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_list_returns_all_ordered_by_id(self):
        a = self.new_category("A")["id"]
        b = self.new_category("B")["id"]
        c = self.new_category("C")["id"]
        r = self.client.get("/api/categories")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([x["id"] for x in r.json()], [a, b, c])


class CategoryUpdateTest(ApiTestCase):
    def test_patch_only_updates_sent_fields(self):
        cid = self.new_category("原名", "原描述")["id"]
        r = self.client.patch(f"/api/categories/{cid}", json={"description": "新描述"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["name"], "原名")  # 未传,不应被改动
        self.assertEqual(body["description"], "新描述")

    def test_patch_not_found(self):
        r = self.client.patch("/api/categories/999999", json={"name": "x"})
        self.assertEqual(r.status_code, 404)

    def test_patch_blank_name_rejected(self):
        cid = self.new_category("某分类")["id"]
        r = self.client.patch(f"/api/categories/{cid}", json={"name": "   "})
        self.assertEqual(r.status_code, 422)

    def test_patch_duplicate_name_conflict(self):
        self.new_category("已占用")
        cid = self.new_category("待改名")["id"]
        r = self.client.patch(f"/api/categories/{cid}", json={"name": "已占用"})
        self.assertEqual(r.status_code, 409)


class CategoryDeleteTest(ApiTestCase):
    def test_delete(self):
        cid = self.new_category("要删掉")["id"]
        r = self.client.delete(f"/api/categories/{cid}")
        self.assertEqual(r.status_code, 204)
        # 再查列表应为空
        r = self.client.get("/api/categories")
        self.assertEqual(r.json(), [])

    def test_delete_not_found(self):
        r = self.client.delete("/api/categories/999999")
        self.assertEqual(r.status_code, 404)
