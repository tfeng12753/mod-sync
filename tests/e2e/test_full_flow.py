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
        self.assertEqual(r["options"][0]["price_change_cents"], 0)
        self.assertNotIn("crispy_tofu", [o["changes"][0]["with"] for o in r["options"]])  # tofu below cutoff

    def test_cheaper_swap_and_extra_keep_base_price_plus_full_upcharge(self):
        response = self.evaluate("chicken_bowl", "swap chicken for mushrooms, extra avocado", qty=2)
        self.assertEqual(response["status"], "ready")
        self.assertEqual(response["as_requested"]["price_change_cents"], 400)
        order = self.client.post(f"/api/orders/{response['order_id']}/accept", json={}).json()
        self.assertEqual(order["status"], "confirmed")
        self.assertEqual(order["evaluation"]["as_requested"]["price_change_cents"], 400)

    def test_menu_advertises_no_negative_modifier_prices(self):
        for dish in self.client.get("/api/menu").json():
            self.assertTrue(all(swap["price_delta_cents"] >= 0 for swap in dish["swaps"]))
            self.assertTrue(all(extra["price_cents"] >= 0 for extra in dish["extras"]))

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

    def followup(self, text, answers, dish="chicken_bowl"):
        history = []
        response = self.client.post("/api/evaluate", json={"dish_id": dish, "text": text}).json()
        for answer in answers:
            history.append({"question": "\n".join(response["questions"]) or response["customer_message"],
                            "answer": answer})
            result = self.client.post("/api/evaluate", json={
                "dish_id": dish, "text": text, "clarifications": history, "channel": "doordash",
            })
            self.assertEqual(result.status_code, 200, result.text)
            response = result.json()
        return response

    def test_unknown_swap_then_known_but_unapproved_extra_then_valid_extra(self):
        response = self.followup("swap chicken for caviar", ["oh then lets add lettuce instead"])
        self.assertEqual(response["status"], "unavailable")
        self.assertEqual(response["parsed"]["ops"], [{"op": "add", "ingredient": "lettuce", "qty": 1}])
        self.assertIsNone(response["order_id"])
        self.assertFalse(response["clarification_exhausted"])
        response = self.followup("swap chicken for caviar", ["add lettuce instead", "add avocado instead"])
        self.assertEqual(response["status"], "ready")
        self.assertEqual(response["warnings"], [])  # full engine flow ran without fallback
        self.assertEqual(response["parsed"]["ops"], [{"op": "add", "ingredient": "avocado", "qty": 1}])
        order = self.client.post(f"/api/orders/{response['order_id']}/accept", json={}).json()
        self.assertEqual(order["status"], "confirmed")
        self.assertEqual(order["channel"], "doordash")
        self.assertIn("EXTRA Sliced avocado x1", order["kitchen_instructions"])
        self.assertEqual(len(order["evaluation"]["clarifications"]), 2)

    def test_bare_replacement_runs_inventory_and_engine(self):
        response = self.followup("swap chicken for caviar", ["tofu"])
        self.assertEqual(response["status"], "ready")
        self.assertEqual(response["warnings"], [])
        self.assertIn("SUB Crispy tofu for Grilled chicken", response["as_requested"]["kitchen_instructions"])

    def test_three_unresolved_replies_end_without_order_or_hold(self):
        response = self.followup("swap chicken for caviar", ["add truffles instead", "add lobster instead", "add gold instead"])
        self.assertEqual(response["status"], "unavailable")
        self.assertTrue(response["clarification_exhausted"])
        self.assertEqual(response["questions"], [])
        self.assertIsNone(response["order_id"])
        self.assertIn("contact restaurant staff", response["customer_message"])
        self.assertEqual(self.service.list_orders(), [])
        self.assertTrue(all(item["available"] == item["on_hand"] for item in self.service.stock()))

    def test_valid_third_reply_succeeds(self):
        response = self.followup("swap chicken for caviar", ["add truffles instead", "add lobster instead", "add avocado instead"])
        self.assertEqual(response["status"], "ready")
        self.assertFalse(response["clarification_exhausted"])
        self.assertIsNotNone(response["order_id"])

    def test_revision_preserves_allergy_from_original_and_earlier_answer(self):
        for text, answers, allergen in [
            ("peanut allergy, swap chicken for caviar", ["add avocado instead"], "peanut"),
            ("swap chicken for caviar", ["sesame allergy", "add avocado instead"], "sesame"),
        ]:
            with self.subTest(allergen=allergen):
                self.setUp()
                response = self.followup(text, answers)
                self.assertEqual(response["status"], "needs_staff_review")
                self.assertIn({"kind": "allergy", "value": allergen}, response["parsed"]["restrictions"])
                order = self.client.post(f"/api/orders/{response['order_id']}/accept", json={})
                self.assertEqual(order.status_code, 409)  # customer cannot bypass staff


if __name__ == "__main__":
    unittest.main()
