"""应用健康检查。"""

from tests.base import ApiTestCase


class HealthTest(ApiTestCase):
    def test_health_ok(self):
        r = self.client.get("/api/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"status": "ok"})
