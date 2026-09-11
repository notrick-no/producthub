"""首页「最近动态」与「项目汇总」(第四版)接口测试。

动态是**写端点多打一行**记下来的 —— 没有架构强制,漏记不会报错。
所以这里逐个写操作地断言「动完之后 /api/activity 里出现这条」,
漏记的风险由这个文件兜住,而不是靠抽象兜住(见第四版定稿)。
"""
from fastapi.testclient import TestClient

from tests.base import ApiTestCase, _ADMIN_EMAIL

_ADMIN_NAME = "测试管理员"


class ActivityRecordTest(ApiTestCase):
    """每个写端点都要留下一条动态。"""

    def _events(self, **params) -> list[dict]:
        r = self.client.get("/api/activity", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_product_create_recorded(self):
        product = self.new_product("Alpha")
        events = self._events()
        self.assertEqual(len(events), 1, events)
        e = events[0]
        self.assertEqual(e["action"], "create")
        self.assertEqual(e["content_type"], "product")
        self.assertEqual(e["content_type_name"], "产品")
        self.assertEqual(e["title"], "Alpha")
        self.assertEqual(e["object_id"], product["id"])
        self.assertEqual(e["url"], f"/products/{product['id']}")
        self.assertEqual(e["actor_name"], _ADMIN_NAME)
        self.assertIsNotNone(e["created_at"])

    def test_product_update_recorded(self):
        product = self.new_product("Alpha")
        self.client.patch(f"/api/products/{product['id']}", json={"name": "Alpha 2"})
        events = self._events()
        self.assertEqual(events[0]["action"], "update")
        self.assertEqual(events[0]["title"], "Alpha 2")  # 标题是当时的快照

    def test_product_delete_recorded_with_null_url(self):
        product = self.new_product("Alpha")
        self.client.delete(f"/api/products/{product['id']}")
        events = self._events()
        self.assertEqual(events[0]["action"], "delete")
        self.assertEqual(events[0]["title"], "Alpha")  # 对象没了,标题还在
        self.assertIsNone(events[0]["url"])  # 删了就不该再给链接

    def test_requirement_create_recorded(self):
        requirement = self.new_requirement("支持导出 CSV")
        e = self._events()[0]
        self.assertEqual(e["action"], "create")
        self.assertEqual(e["content_type"], "requirement")
        self.assertEqual(e["content_type_name"], "需求")
        self.assertEqual(e["title"], "支持导出 CSV")
        self.assertEqual(e["url"], f"/requirements/{requirement['id']}")

    def test_requirement_update_recorded(self):
        requirement = self.new_requirement("支持导出 CSV")
        self.client.patch(
            f"/api/requirements/{requirement['id']}", json={"status": "已排期"}
        )
        self.assertEqual(self._events()[0]["action"], "update")

    def test_requirement_delete_recorded_with_null_url(self):
        requirement = self.new_requirement("支持导出 CSV")
        self.client.delete(f"/api/requirements/{requirement['id']}")
        events = self._events()
        self.assertEqual(events[0]["action"], "delete")
        self.assertEqual(events[0]["title"], "支持导出 CSV")
        self.assertIsNone(events[0]["url"])
        # 需求本身没了,但动态还在
        self.assertEqual(
            self.client.get(f"/api/requirements/{requirement['id']}").status_code, 404
        )

    def test_failed_write_records_nothing(self):
        """校验不通过(422)/ 找不到(404)的写操作不该留下动态。"""
        self.client.post("/api/requirements", json={"description": "   "})
        self.client.patch("/api/requirements/999999", json={"status": "已完成"})
        self.client.delete("/api/requirements/999999")
        self.assertEqual(self._events(), [])

    def test_category_changes_not_recorded(self):
        """分类是标签不是内容,不进动态流 —— 否则会被「新建分类」刷屏。"""
        category = self.new_category("AI 工具")
        self.client.patch(f"/api/categories/{category['id']}", json={"name": "AI"})
        self.client.delete(f"/api/categories/{category['id']}")
        self.assertEqual(self._events(), [])

    def test_actor_name_is_snapshot(self):
        """用户改名后,历史动态仍显示当时的名字。"""
        self.new_requirement("支持导出 CSV")
        admin_id = next(
            u["id"] for u in self.client.get("/api/users").json() if u["email"] == _ADMIN_EMAIL
        )
        r = self.client.patch(f"/api/users/{admin_id}", json={"name": "改名后的管理员"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(self._events()[0]["actor_name"], _ADMIN_NAME)


class ActivityListTest(ApiTestCase):
    def _events(self, **params) -> list[dict]:
        r = self.client.get("/api/activity", params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_empty(self):
        self.assertEqual(self._events(), [])

    def test_newest_first(self):
        self.new_requirement("第一条")
        self.new_requirement("第二条")
        self.new_requirement("第三条")
        self.assertEqual(
            [e["title"] for e in self._events()], ["第三条", "第二条", "第一条"]
        )

    def test_limit_and_offset(self):
        for i in range(5):
            self.new_requirement(f"第 {i} 条")
        page = self._events(limit=2)
        self.assertEqual([e["title"] for e in page], ["第 4 条", "第 3 条"])
        nxt = self._events(limit=2, offset=2)
        self.assertEqual([e["title"] for e in nxt], ["第 2 条", "第 1 条"])

    def test_default_limit_is_20(self):
        for i in range(25):
            self.new_requirement(f"第 {i} 条")
        self.assertEqual(len(self._events()), 20)

    def test_invalid_limit_rejected(self):
        self.assertEqual(self.client.get("/api/activity", params={"limit": 0}).status_code, 422)
        self.assertEqual(self.client.get("/api/activity", params={"limit": 999}).status_code, 422)
        self.assertEqual(self.client.get("/api/activity", params={"offset": -1}).status_code, 422)


class SummaryTest(ApiTestCase):
    def _summary(self) -> list[dict]:
        r = self.client.get("/api/summary")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_shape_and_order(self):
        """汇总按 content_types.py 的顺序返回,本版只有产品和需求。"""
        summary = self._summary()
        self.assertEqual([x["key"] for x in summary], ["product", "requirement"])
        self.assertEqual([x["name"] for x in summary], ["产品", "需求"])
        self.assertEqual([x["url"] for x in summary], ["/products", "/requirements"])

    def test_counts_start_at_zero(self):
        self.assertEqual([x["count"] for x in self._summary()], [0, 0])

    def test_counts_follow_data(self):
        self.new_product("Alpha")
        self.new_product("Beta")
        self.new_requirement("需求一")
        self.assertEqual([x["count"] for x in self._summary()], [2, 1])

    def test_counts_follow_deletes(self):
        product = self.new_product("Alpha")
        self.new_requirement("需求一")
        self.client.delete(f"/api/products/{product['id']}")
        self.assertEqual([x["count"] for x in self._summary()], [0, 1])


class HomeAuthTest(ApiTestCase):
    def test_anonymous_gets_401(self):
        from app.main import app

        client = TestClient(app)
        self.assertEqual(client.get("/api/activity").status_code, 401)
        self.assertEqual(client.get("/api/summary").status_code, 401)
