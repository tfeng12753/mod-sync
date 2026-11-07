"""API + orchestration tests with fake parser/engine steps (offline, no LLM, no forecasting).

The fake parser returns the expected output from fixtures/parser_cases.json for known texts, so
these tests exercise the real inventory and order lifecycle end to end over HTTP.
"""

import json
import unittest
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.service import OrderService
from backend.inventory import load_inventory

CASES = {c["text"]: c for c in json.loads(
    (Path(__file__).resolve().parents[2] / "fixtures" / "parser_cases.json").read_text(encoding="utf-8"))["cases"]}


@dataclass
class FakeParsed:
    status: str
    parser_output: dict
    questions: list = field(default_factory=list)
    staff_notes: list = field(default_factory=list)
    restrictions: list = field(default_factory=list)
    source: str = "fake"


def fake_parse(inv, dish_id, qty, text, clarifications):
    if text == "PARSER_BOOM":
        raise ValueError("model returned junk")
    if text == "ASK_ME":
        return FakeParsed("needs_clarification", {"dish": dish_id, "qty": qty, "ops": []},
                          questions=["Which sauce do you mean?"])
    expected = CASES[text]["expected"] if text in CASES else {"ops": [], "flags": None}
    flags = expected["flags"] or {}
    restrictions = [{"kind": "allergy", "value": a} for a in flags.get("allergens", [])] + \
                   [{"kind": "diet", "value": d} for d in flags.get("diets", [])]
    status = "needs_staff_review" if restrictions else "ready"
    notes = ["Customer mentioned an allergy or diet."] if restrictions else []
    return FakeParsed(status, {"dish": dish_id, "qty": qty, "original_text": text, **expected},
                      staff_notes=notes, restrictions=restrictions)


async def fake_route(inv, evaluation):
    review = evaluation.requires_staff_review
    choices = []
    if evaluation.check.ok:
        choices.append(SimpleNamespace(option_index=None, route="staff" if review else "customer", label="as asked",
                                       score=None, similarity=None, price_change_cents=evaluation.check.price_delta_cents,
                                       reasons=[], staff_notes=[]))
    for i, opt in enumerate(evaluation.options):
        choices.append(SimpleNamespace(option_index=i, route="staff" if review else "customer", label=f"option {i}",
                                       score=0.8 - i * 0.01, similarity=opt.similarity,
                                       price_change_cents=opt.price_delta_cents,
                                       reasons=["fake forecast reason"], staff_notes=[]))
    return SimpleNamespace(status="reply_to_customer", customer_message="ok", choices=choices, unavailable=[],
                           staff_review_notes=["engine note"] if review else [],
                           expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                           forecast_summary={}, raw=[])


async def broken_route(inv, evaluation):
    raise RuntimeError("engine down")


def make_client(route=fake_route):
    service = OrderService(inv=load_inventory(), parse_fn=fake_parse, route_fn=route)
    return TestClient(create_app(service)), service


def evaluate(client, dish, text, qty=1, **extra):
    return client.post("/api/evaluate", json={"dish_id": dish, "qty": qty, "text": text, **extra})


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client, self.service = make_client()

    def send_to_staff(self, result, option_id=None):
        choice = option_id if option_id is not None else result["recommended_option_id"]
        response = self.client.post(f"/api/orders/{result['order_id']}/accept", json={"option_id": choice})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "awaiting_staff")

    def test_health_and_menu(self):
        self.assertEqual(self.client.get("/api/health").json(), {"status": "ok"})
        menu = self.client.get("/api/menu").json()
        self.assertEqual({d["id"] for d in menu},
                         {"chicken_bowl", "tofu_bowl", "satay_noodles", "falafel_wrap", "miso_soup"})

    def test_ready_then_accept_confirms_and_deducts(self):
        before = self.service.inv.stock.on_hand("avocado")
        r = evaluate(self.client, "chicken_bowl", "extra avo!!").json()
        self.assertEqual(r["status"], "ready")
        self.assertTrue(r["as_requested"]["feasible"])
        self.assertIsNotNone(r["order_id"])
        order = self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": None}).json()
        self.assertEqual(order["status"], "confirmed")
        self.assertEqual(self.service.inv.stock.on_hand("avocado"), before - 120)
        self.assertIn("EXTRA Sliced avocado x1", order["kitchen_instructions"])

    def test_options_then_customer_picks_one(self):
        r = evaluate(self.client, "chicken_bowl", "no garlic").json()
        self.assertEqual(r["status"], "needs_customer_approval")
        self.assertEqual(r["recommended_option_id"], "opt-1")
        self.assertEqual(r["options"][0]["changes"][0]["with"], "sesame_dressing")
        self.assertFalse(r["as_requested"]["feasible"])
        order = self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": "opt-1"}).json()
        self.assertEqual(order["status"], "confirmed")
        self.assertIn("SUB Sesame dressing for Garlic sauce", order["kitchen_instructions"])

    def test_cannot_accept_as_requested_when_infeasible(self):
        r = evaluate(self.client, "chicken_bowl", "no garlic").json()
        res = self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": None})
        self.assertEqual(res.status_code, 400)

    def test_allergy_customer_then_staff_then_final_customer_confirmation(self):
        r = evaluate(self.client, "satay_noodles", "my kid is allergic to peanuts").json()
        self.assertEqual(r["status"], "needs_customer_approval")
        self.assertTrue(r["review"]["required"])
        self.assertIn("allergy: peanut", r["review"]["reasons"])
        self.assertIn("engine note", r["review"]["staff_notes"])
        self.assertTrue(all(o["route"] == "staff" for o in r["options"]))
        self.assertNotIn("peanut", r["review"]["evidence"]["allergens_present"])
        queue = [o for o in self.client.get("/api/orders").json() if o["status"] == "awaiting_staff"]
        self.assertEqual(queue, [])
        self.assertEqual(self.client.post(f"/api/orders/{r['order_id']}/review", json={"action": "approve"}).status_code, 409)
        self.send_to_staff(r)
        before = self.service.inv.stock.on_hand("sesame_dressing")
        order = self.client.post(f"/api/orders/{r['order_id']}/review", json={"action": "approve"}).json()
        self.assertEqual(order["status"], "awaiting_customer")
        self.assertEqual(self.service.inv.stock.on_hand("sesame_dressing"), before)
        order = self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": r["recommended_option_id"]}).json()
        self.assertEqual(order["status"], "confirmed")
        self.assertLess(self.service.inv.stock.on_hand("sesame_dressing"), before)
        self.assertEqual(order["kitchen_instructions"][0], "ALLERGY: peanut")

    def test_staff_edit_switches_option(self):
        r = evaluate(self.client, "satay_noodles", "my kid is allergic to peanuts").json()
        self.send_to_staff(r)
        second = r["options"][1]["option_id"]
        order = self.client.post(f"/api/orders/{r['order_id']}/review",
                                 json={"action": "edit", "option_id": second}).json()
        self.assertEqual(order["status"], "awaiting_customer")
        self.assertEqual(order["selected_option_id"], second)
        self.assertEqual(self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": r["recommended_option_id"]}).status_code, 409)
        self.assertEqual(self.service.get_order(r["order_id"])["selected_option_id"], second)
        order = self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": second}).json()
        self.assertEqual(order["status"], "confirmed")

    def test_staff_reject_releases_hold(self):
        r = evaluate(self.client, "satay_noodles", "my kid is allergic to peanuts").json()
        self.send_to_staff(r)
        sauce = r["options"][0]["changes"][1]["with"]
        held = self.service.inv.available(sauce)
        order = self.client.post(f"/api/orders/{r['order_id']}/review",
                                 json={"action": "reject", "note": "shared wok"}).json()
        self.assertEqual(order["status"], "rejected")
        self.assertGreater(self.service.inv.available(sauce), held)

    def test_customer_cannot_review_and_staff_cannot_accept_twice(self):
        r = evaluate(self.client, "chicken_bowl", "extra avo!!").json()
        self.assertEqual(self.client.post(f"/api/orders/{r['order_id']}/review",
                                          json={"action": "approve"}).status_code, 409)
        self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": None})
        self.assertEqual(self.client.post(f"/api/orders/{r['order_id']}/accept",
                                          json={"option_id": None}).status_code, 409)

    def test_as_requested_diet_needs_both_customer_confirmations(self):
        def diet_parse(inv, dish_id, qty, text, clarifications):
            return FakeParsed("needs_staff_review", {"dish": dish_id, "qty": qty, "ops": [],
                              "flags": {"level": "diet", "diets": ["vegan"]}},
                              staff_notes=["Review vegan preparation"], restrictions=[{"kind": "diet", "value": "vegan"}])
        self.service.parse_fn = diet_parse
        r = evaluate(self.client, "tofu_bowl", "vegan please").json()
        self.assertEqual(r["status"], "ready")
        self.assertTrue(r["review"]["required"])
        self.send_to_staff(r)
        response = self.client.post(f"/api/orders/{r['order_id']}/review", json={"action": "approve"})
        self.assertEqual(response.json()["status"], "awaiting_customer")
        self.assertEqual(response.json()["staff_approved_choice"], "as_requested")
        order = self.client.post(f"/api/orders/{r['order_id']}/accept", json={}).json()
        self.assertEqual(order["status"], "confirmed")

    def test_staff_approved_order_can_be_cancelled_without_deduction(self):
        r = evaluate(self.client, "satay_noodles", "my kid is allergic to peanuts").json()
        before = self.service.inv.stock.on_hand("sesame_dressing")
        self.send_to_staff(r)
        self.client.post(f"/api/orders/{r['order_id']}/review", json={"action": "approve"})
        self.assertEqual(self.client.post(f"/api/orders/{r['order_id']}/cancel").json()["status"], "cancelled")
        self.assertEqual(self.service.inv.stock.on_hand("sesame_dressing"), before)
        self.assertEqual(self.service.inv.available("sesame_dressing"), before)

    def test_allergy_with_unresolved_question_does_not_create_order(self):
        self.service.parse_fn = lambda *args: FakeParsed("needs_staff_review",
            {"dish": "chicken_bowl", "qty": 1, "ops": []},
            questions=["Which sauce did you mean?"], staff_notes=["Review allergy"],
            restrictions=[{"kind": "allergy", "value": "peanut"}])
        r = evaluate(self.client, "chicken_bowl", "peanut allergy, no sauce").json()
        self.assertEqual(r["status"], "needs_clarification")
        self.assertTrue(r["review"]["required"])
        self.assertIsNone(r["order_id"])
        self.assertEqual(self.service.list_orders(), [])

    def test_final_customer_acceptance_rechecks_stock(self):
        r = evaluate(self.client, "satay_noodles", "my kid is allergic to peanuts").json()
        self.send_to_staff(r)
        self.client.post(f"/api/orders/{r['order_id']}/review", json={"action": "approve"})
        self.service.inv.release(r["order_id"])
        self.service.inv.set_on_hand("sesame_dressing", 0, "stock correction")
        res = self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": r["recommended_option_id"]})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.service.get_order(r["order_id"])["status"], "awaiting_customer")

    def test_clarification_from_parser_and_inventory(self):
        r = evaluate(self.client, "chicken_bowl", "ASK_ME").json()
        self.assertEqual(r["status"], "needs_clarification")
        self.assertEqual(r["questions"], ["Which sauce do you mean?"])
        self.assertIsNone(r["order_id"])
        r = evaluate(self.client, "chicken_bowl", "add bacon").json()
        self.assertEqual(r["status"], "needs_clarification")
        self.assertTrue(r["questions"])

    def test_unavailable(self):
        r = evaluate(self.client, "miso_soup", "is this vegan? I need it vegan").json()
        self.assertEqual(r["status"], "unavailable")
        self.assertIsNone(r["order_id"])
        self.assertIn("cannot accommodate", r["customer_message"])
        self.assertEqual(self.client.get("/api/orders").json(), [])

    def test_parser_failure_is_502(self):
        res = evaluate(self.client, "chicken_bowl", "PARSER_BOOM")
        self.assertEqual(res.status_code, 502)

    def test_unknown_dish_and_order(self):
        self.assertEqual(evaluate(self.client, "pizza", "hi").status_code, 404)
        self.assertEqual(self.client.get("/api/orders/ord-9999").status_code, 404)

    def test_stock_controls_and_reevaluation(self):
        self.client.post("/api/stock/tofu", json={"on_hand": 0, "reason": "ran out"})
        item = next(i for i in self.client.get("/api/stock").json()["items"] if i["id"] == "tofu")
        self.assertEqual(item["on_hand"], 0)
        r = evaluate(self.client, "chicken_bowl", "swap the chicken for tofu").json()
        self.assertEqual(r["status"], "needs_customer_approval")  # tofu gone -> mushrooms offered
        self.assertEqual(r["options"][0]["changes"][0]["with"], "roasted_mushrooms")

    def test_stock_change_after_evaluation_blocks_confirm(self):
        r = evaluate(self.client, "chicken_bowl", "swap the chicken for tofu").json()
        self.assertEqual(r["status"], "ready")
        # Holds protect the order: other orders can't take it...
        self.service.inv.release(r["order_id"])  # ...unless the hold is gone, e.g. staff dropped the tray
        self.client.post("/api/stock/tofu", json={"on_hand": 50})
        res = self.client.post(f"/api/orders/{r['order_id']}/accept", json={"option_id": None})
        self.assertEqual(res.status_code, 409)

    def test_hold_expiry_marks_order_expired(self):
        r = evaluate(self.client, "satay_noodles", "my kid is allergic to peanuts").json()
        order = self.service.orders.get(r["order_id"])
        order.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.assertEqual(self.client.get(f"/api/orders/{r['order_id']}").json()["status"], "expired")
        self.assertEqual(self.client.post(f"/api/orders/{r['order_id']}/review",
                                          json={"action": "approve"}).status_code, 409)

    def test_cancel_releases_hold(self):
        r = evaluate(self.client, "chicken_bowl", "no garlic").json()
        held = self.service.inv.available("sesame_dressing")
        order = self.client.post(f"/api/orders/{r['order_id']}/cancel").json()
        self.assertEqual(order["status"], "cancelled")
        self.assertGreater(self.service.inv.available("sesame_dressing"), held)
        self.assertEqual(self.client.post(f"/api/orders/{r['order_id']}/cancel").status_code, 409)

    def test_reject_note_and_initial_stock(self):
        r = evaluate(self.client, "satay_noodles", "my kid is allergic to peanuts").json()
        self.send_to_staff(r)
        order = self.client.post(f"/api/orders/{r['order_id']}/review",
                                 json={"action": "reject", "note": "shared wok"}).json()
        self.assertEqual(order["staff_note"], "shared wok")
        self.client.post("/api/stock/tofu", json={"on_hand": 0})
        tofu = next(i for i in self.client.get("/api/stock").json()["items"] if i["id"] == "tofu")
        self.assertEqual((tofu["on_hand"], tofu["initial_on_hand"]), (0, 900))

    def test_reset(self):
        evaluate(self.client, "chicken_bowl", "extra avo!!")
        self.client.post("/api/stock/tofu", json={"on_hand": 0})
        self.assertEqual(self.client.post("/api/demo/reset").json(), {"status": "reset"})
        self.assertEqual(self.client.get("/api/orders").json(), [])
        stock = self.client.get("/api/stock").json()["items"]
        self.assertTrue(all(item["on_hand"] == item["initial_on_hand"] for item in stock))
        self.assertTrue(all(item["available"] == item["on_hand"] for item in stock))

    def test_engine_failure_cannot_bypass_combined_score_cutoff(self):
        client, _ = make_client(route=broken_route)
        r = evaluate(client, "chicken_bowl", "no chicken, can you put something else instead").json()
        self.assertEqual(r["status"], "unavailable")
        self.assertTrue(r["warnings"])
        self.assertEqual(r["options"], [])
        self.assertIsNone(r["order_id"])


if __name__ == "__main__":
    unittest.main()
