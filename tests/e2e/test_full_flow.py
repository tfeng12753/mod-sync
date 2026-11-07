"""End to end over HTTP with the REAL pieces: offline parser -> inventory -> forecast engine -> API.

No LLM calls (parser forced to offline mode). Uses the synthetic catalog, stock and usage history.
"""

import unittest

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.service import OrderService
from backend.inventory import load_inventory
from backend.parsing.adapter import parse_request


def offline_parse(inv, dish_id, qty, text, clarifications):
    return parse_request(inv, dish_id, qty, text, clarifications, mode="offline")


class FullFlowTests(unittest.TestCase):
    def setUp(self):
        self.service = OrderService(inv=load_inventory(), parse_fn=offline_parse)  # real engine routing
        self.client = TestClient(create_app(self.service))

    def evaluate(self, dish, text, qty=1):
        res = self.client.post("/api/evaluate", json={"dish_id": dish, "qty": qty, "text": text})
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual(body["warnings"], [], "engine routing should not fall back")
        return body

    def test_1_close_substitute_as_asked(self):
        r = self.evaluate("chicken_bowl", "swap the chicken for tofu")
        self.assertEqual(r["status"], "ready")
        order = self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": None}).json()
        self.assertEqual(order["status"], "confirmed")
        self.assertIn("SUB Crispy tofu for Grilled chicken", order["kitchen_instructions"])

    def test_2_premade_garlic_offers_sesame_dressing(self):
        r = self.evaluate("chicken_bowl", "no garlic")
        self.assertEqual(r["status"], "needs_customer_approval")
        self.assertEqual(r["options"][0]["changes"][0]["with"], "sesame_dressing")
        self.assertGreaterEqual(r["options"][0]["score"], 0.75)

    def test_3_forecast_prefers_mushrooms_over_tofu(self):
        r = self.evaluate("chicken_bowl", "no chicken, can you put something else instead")
        self.assertEqual(r["status"], "needs_customer_approval")
        self.assertEqual(r["options"][0]["changes"][0]["with"], "roasted_mushrooms")
        self.assertEqual(r["options"][0]["price_change_cents"], -100)
        self.assertNotIn("crispy_tofu", [o["changes"][0]["with"] for o in r["options"]])  # tofu below cutoff

    def test_bare_swap_offers_options(self):
        r = self.evaluate("chicken_bowl", "can you swap the chicken")
        self.assertEqual(r["status"], "needs_customer_approval")
        self.assertEqual(r["parsed"]["ops"], [{"op": "substitute", "ingredient": "chicken", "with": None}])

    def test_4_stock_change_forces_reevaluation(self):
        r = self.evaluate("chicken_bowl", "no chicken, can you put something else instead")
        self.service.inv.release(r["order_id"])
        self.client.post("/api/stock/mushrooms", json={"on_hand": 100, "reason": "dropped"})
        res = self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": "opt-1"})
        self.assertEqual(res.status_code, 409)
        again = self.evaluate("chicken_bowl", "no chicken, can you put something else instead")
        self.assertEqual(again["status"], "unavailable")  # tofu alone is below the cutoff tonight

    def test_peanut_allergy_staff_flow(self):
        r = self.evaluate("satay_noodles", "my kid is allergic to peanuts")
        self.assertEqual(r["status"], "needs_staff_review")
        self.assertEqual(r["parsed"]["restrictions"], [{"kind": "allergy", "value": "peanut"}])
        first = r["options"][0]
        self.assertEqual(first["route"], "staff")
        self.assertEqual([(c["replace"], c["with"]) for c in first["changes"]],
                         [("crushed_peanuts", None), ("peanut_sauce", "sesame_dressing")])
        self.assertNotIn("peanut", r["review"]["evidence"]["allergens_present"])
        self.assertTrue(r["review"]["staff_notes"])
        order = self.client.post(f"/api/orders/{r['order_id']}/review", json={"action": "approve"}).json()
        self.assertEqual(order["status"], "confirmed")
        self.assertEqual(order["kitchen_instructions"][0], "ALLERGY: peanut")

    def test_unknown_item_asks_customer(self):
        r = self.evaluate("chicken_bowl", "add bacon")
        self.assertEqual(r["status"], "needs_clarification")
        self.assertIn('"bacon"', r["questions"][0])

    def test_unmeetable_diet_is_unavailable(self):
        r = self.evaluate("miso_soup", "I need it vegan")
        self.assertEqual(r["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
