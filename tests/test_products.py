"""产品 CRUD + 打标 接口测试。"""

from tests.base import ApiTestCase


def _category_names(product: dict) -> list[str]:
    return [c["name"] for c in product["categories"]]


class ProductCreateTest(ApiTestCase):
    def test_create_minimal(self):
        r = self.client.post("/api/products", json={"name": "Notion"})
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["name"], "Notion")
        self.assertIsNone(body["url"])
        self.assertEqual(body["categories"], [])
        self.assertIsNotNone(body["id"])

    def test_create_with_fields(self):
        r = self.client.post(
            "/api/products",
            json={
                "name": "Product Hunt",
                "url": "producthunt.com",  # 宽松:不带 scheme 也接受
                "founder": "Ryan Hoover",
                "monthly_visits": 5000000,
                "problem": "产品发现",
            },
        )
        self.assertEqual(r.status_code, 201, r.text)
        body = r.json()
        self.assertEqual(body["founder"], "Ryan Hoover")
        self.assertEqual(body["monthly_visits"], 5000000)
        self.assertEqual(body["url"], "producthunt.com")

    def test_create_with_category_ids_returns_full_categories(self):
        c1 = self.new_category("AI")["id"]
        c2 = self.new_category("工具")["id"]
        body = self.new_product("示例", category_ids=[c2, c1])  # 乱序传入
        self.assertEqual(_category_names(body), ["AI", "工具"])  # 按 id 升序返回

    def test_create_with_nonexistent_category_rejected(self):
        r = self.client.post(
            "/api/products", json={"name": "x", "category_ids": [1, 999]}
        )
        self.assertEqual(r.status_code, 400, r.text)

    def test_create_blank_name_rejected(self):
        r = self.client.post("/api/products", json={"name": "   "})
        self.assertEqual(r.status_code, 422)

    def test_create_negative_monthly_visits_rejected(self):
        r = self.client.post(
            "/api/products", json={"name": "x", "monthly_visits": -1}
        )
        self.assertEqual(r.status_code, 422)


class ProductListTest(ApiTestCase):
    def test_empty_list(self):
        r = self.client.get("/api/products")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    def test_list_ordered_newest_first(self):
        a = self.new_product("A")["id"]
        b = self.new_product("B")["id"]
        r = self.client.get("/api/products")
        self.assertEqual([p["id"] for p in r.json()], [b, a])

    def test_list_filter_by_category(self):
        c1 = self.new_category("AI")["id"]
        c2 = self.new_category("工具")["id"]
        self.new_product("OnlyAI", category_ids=[c1])
        self.new_product("Both", category_ids=[c1, c2])

        r = self.client.get("/api/products", params={"category_id": c2})
        self.assertEqual(r.status_code, 200)
        names = [p["name"] for p in r.json()]
        self.assertEqual(names, ["Both"])

    def test_list_filter_unknown_category_empty(self):
        self.new_product("x")
        r = self.client.get("/api/products", params={"category_id": 999})
        self.assertEqual(r.json(), [])


class ProductGetTest(ApiTestCase):
    def test_get_detail_with_categories(self):
        cid = self.new_category("AI")["id"]
        pid = self.new_product("Notion", category_ids=[cid])["id"]
        r = self.client.get(f"/api/products/{pid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(_category_names(r.json()), ["AI"])

    def test_get_not_found(self):
        r = self.client.get("/api/products/999999")
        self.assertEqual(r.status_code, 404)


class ProductUpdateTest(ApiTestCase):
    def test_patch_scalar_only_keeps_categories(self):
        cid = self.new_category("AI")["id"]
        p = self.new_product("原名", category_ids=[cid])
        r = self.client.patch(
            f"/api/products/{p['id']}",
            json={"name": "新名", "problem": "新问题"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["name"], "新名")
        self.assertEqual(body["problem"], "新问题")
        self.assertEqual(_category_names(body), ["AI"])  # 未传 category_ids,分类不动

    def test_patch_category_ids_empty_clears(self):
        cid = self.new_category("AI")["id"]
        p = self.new_product("Notion", category_ids=[cid])
        r = self.client.patch(f"/api/products/{p['id']}", json={"category_ids": []})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["categories"], [])

    def test_patch_category_ids_replaces(self):
        old = self.new_category("旧")["id"]
        new = self.new_category("新")["id"]
        p = self.new_product("Notion", category_ids=[old])
        r = self.client.patch(f"/api/products/{p['id']}", json={"category_ids": [new]})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(_category_names(r.json()), ["新"])

    def test_patch_nonexistent_category_rejected_and_unchanged(self):
        old = self.new_category("AI")["id"]
        p = self.new_product("Notion", category_ids=[old])
        r = self.client.patch(
            f"/api/products/{p['id']}", json={"name": "会改吗", "category_ids": [999]}
        )
        self.assertEqual(r.status_code, 400, r.text)
        # 校验失败不应留下任何部分修改
        detail = self.client.get(f"/api/products/{p['id']}").json()
        self.assertEqual(detail["name"], "Notion")
        self.assertEqual(_category_names(detail), ["AI"])

    def test_patch_empty_url_clears_it(self):
        p = self.new_product("Notion", url="https://notion.so")
        r = self.client.patch(f"/api/products/{p['id']}", json={"url": ""})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsNone(r.json()["url"])

    def test_patch_not_found(self):
        r = self.client.patch("/api/products/999999", json={"name": "x"})
        self.assertEqual(r.status_code, 404)

    def test_patch_blank_name_rejected(self):
        p = self.new_product("Notion")
        r = self.client.patch(f"/api/products/{p['id']}", json={"name": "  "})
        self.assertEqual(r.status_code, 422)


class ProductDeleteTest(ApiTestCase):
    def test_delete(self):
        p = self.new_product("要删")
        r = self.client.delete(f"/api/products/{p['id']}")
        self.assertEqual(r.status_code, 204)
        self.assertEqual(self.client.get("/api/products").json(), [])

    def test_delete_not_found(self):
        r = self.client.delete("/api/products/999999")
        self.assertEqual(r.status_code, 404)

    def test_delete_product_keeps_category(self):
        cid = self.new_category("AI")["id"]
        p = self.new_product("Notion", category_ids=[cid])
        self.client.delete(f"/api/products/{p['id']}")
        # 分类还在
        cats = self.client.get("/api/categories").json()
        self.assertEqual([c["id"] for c in cats], [cid])

    def test_delete_category_unlinks_from_product(self):
        cid = self.new_category("AI")["id"]
        p = self.new_product("Notion", category_ids=[cid])
        self.client.delete(f"/api/categories/{cid}")
        # 产品还在,分类被清空
        detail = self.client.get(f"/api/products/{p['id']}")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["categories"], [])
