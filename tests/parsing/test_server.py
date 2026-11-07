"""The standalone /parse service still works (fake model, no network)."""

import unittest
from unittest import mock

from fastapi.testclient import TestClient

from backend.parsing import parser, server

from .helpers import FakeClient, change, model_output

REQUEST = {
    "instructions": "no avocado, sauce on the side",
    "cart": [{"cart_item_id": "chicken_bowl", "name": "Garlic Chicken Bowl",
              "ingredients": {"sliced_avocado": 1, "avocado": 1, "garlic_sauce": 1}}],
    "allowed_ingredient_ids": ["sliced_avocado", "avocado", "garlic_sauce"],
}


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.http = TestClient(server.app)

    def post_with(self, output, body=REQUEST):
        fake = FakeClient(output)
        real = parser.parse_modifications
        with mock.patch.object(server, "parse_modifications", side_effect=lambda req: real(req, client=fake)):
            return self.http.post("/parse", json=body)

    def test_health(self):
        self.assertEqual(self.http.get("/health").json()["status"], "ok")

    def test_parse_ok(self):
        resp = self.post_with(model_output(changes=[change("chicken_bowl", "avocado", "remove"),
                                                    change("chicken_bowl", "garlic_sauce", "on_side")]))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "ready")
        self.assertEqual([c["operation"] for c in data["changes"]], ["remove", "on_side"])
        self.assertEqual(data["restrictions"], [])

    def test_unsafe_output_is_502(self):
        resp = self.post_with(model_output(changes=[change("chicken_bowl", "bacon", "add", 1)]))
        self.assertEqual(resp.status_code, 502)

    def test_invalid_request_is_422(self):
        self.assertEqual(self.http.post("/parse", json={**REQUEST, "cart": []}).status_code, 422)


if __name__ == "__main__":
    unittest.main()
