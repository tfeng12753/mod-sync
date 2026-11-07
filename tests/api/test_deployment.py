"""Same-origin deployment and process-local startup regressions."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
import time

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.service import OrderService


class DeploymentTests(TestCase):
    def test_ui_and_api_share_one_origin_without_masking_errors(self):
        with TemporaryDirectory() as directory:
            dist = Path(directory)
            (dist / "assets").mkdir()
            (dist / "index.html").write_text('<html>ModSync</html>')
            (dist / "assets" / "app.js").write_text('console.log("app")')
            client = TestClient(create_app(frontend_dist=dist))
            self.assertIn("ModSync", client.get("/").text)
            self.assertEqual(client.get("/assets/app.js").status_code, 200)
            self.assertEqual(client.get("/api/health").json(), {"status": "ok"})
            self.assertEqual(client.get("/api/menu").status_code, 200)
            self.assertEqual(client.get("/api/not-a-route").status_code, 404)
            self.assertEqual(client.get("/assets/missing.js").status_code, 404)
            self.assertEqual(client.get("/../requirements.txt").status_code, 404)

    def test_missing_frontend_build_fails_startup(self):
        with TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "FRONTEND_DIST"):
                create_app(frontend_dist=Path(directory))

    def test_concurrent_first_requests_share_one_service(self):
        service = OrderService()
        def construct():
            time.sleep(.03)
            return service
        with patch("backend.api.app.OrderService", side_effect=construct) as factory:
            client = TestClient(create_app())
            with ThreadPoolExecutor(max_workers=6) as pool:
                responses = list(pool.map(lambda _: client.get("/api/menu"), range(6)))
            self.assertTrue(all(r.status_code == 200 for r in responses))
            self.assertEqual(factory.call_count, 1)
            self.assertIs(client.app.state.service, service)
