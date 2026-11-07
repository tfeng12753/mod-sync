import asyncio
import copy
import json
import unittest
from pathlib import Path

from backend.engine import (
    ConnectedEvaluationRequest, EvaluationRequest, InventoryGateway, OpenRouterExplainer,
    OutlookRequest, assess_extra, build_inventory_outlook, evaluate_substitutions,
    evaluate_with_inventory,
)
from backend.engine.constraints import SAFETY_REVIEW_NOTE


def payload():
    raw = json.loads((Path(__file__).resolve().parents[1] / "fixtures/evaluation_request.json").read_text())
    raw["forecast"]["model"] = "historical"
    return raw


def restricted(kind="allergy", value="soy"):
    raw = payload()
    raw["restrictions"] = [{"kind": kind, "value": value,
                            "staff_review_note": "Person 2: restaurant staff must review this request."}]
    raw["ingredient_profiles"] = [
        {"ingredient_id": name, "unit": "g", "verified": True,
         "allergen_status": {"soy": name == "tofu"}, "dietary_status": {"vegan": True}}
        for name in ["tofu", "mushrooms", "rice"]
    ]
    return raw


def evaluate(raw, **kwargs):
    return asyncio.run(evaluate_substitutions(EvaluationRequest.model_validate(raw), **kwargs))


class RestrictionTests(unittest.TestCase):
    def test_same_allergen_substitution_is_blocked_despite_high_similarity(self):
        raw = restricted()
        raw["candidates"][1]["similarity"] = 1
        result = evaluate(raw)
        self.assertIsNone(result.recommended_option_id)
        self.assertEqual(result.status, "needs_staff_review")
        self.assertEqual([o.option_id for o in result.review_options], ["mushroom_bowl"])
        self.assertEqual([o.option_id for o in result.excluded_options], ["tofu_bowl"])
        self.assertTrue(result.excluded_options[0].blocked_by_restriction)
        self.assertIn(SAFETY_REVIEW_NOTE, result.staff_review_notes)
        self.assertIn("Person 2", result.review_options[0].staff_review_notes[0])

    def test_complete_recipe_rechecked_for_dietary_restrictions(self):
        raw = restricted("dietary", "vegan")
        raw["ingredient_profiles"][2]["dietary_status"]["vegan"] = False
        result = evaluate(raw)
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.review_options, [])
        self.assertTrue(all(o.blocked_by_restriction for o in result.excluded_options))

    def test_unknown_or_unverified_ingredient_data_never_implies_safety(self):
        for field, value in [("verified", False), ("allergen_status", {})]:
            raw = restricted()
            raw["ingredient_profiles"][1][field] = value
            result = evaluate(raw)
            self.assertEqual(result.review_options, [])
            self.assertEqual(result.ranked_options, [])
            self.assertIsNone(result.recommended_option_id)

    def test_dietary_pass_still_requires_staff(self):
        result = evaluate(restricted("dietary", "vegan"))
        self.assertEqual(len(result.review_options), 2)
        self.assertTrue(all(not o.eligible and o.requires_staff_review for o in result.review_options))
        self.assertEqual(result.status, "needs_staff_review")

    def test_missing_note_is_replaced_by_mandatory_safety_note(self):
        raw = restricted()
        raw["restrictions"][0].pop("staff_review_note")
        result = evaluate(raw)
        self.assertIn(SAFETY_REVIEW_NOTE, result.staff_review_notes)

    def test_extra_restrictions_preserve_staff_review(self):
        raw = restricted("dietary", "vegan")
        outlook = build_inventory_outlook(OutlookRequest.model_validate({
            key: raw[key] for key in ["evaluated_at", "forecast", "inventory", "restrictions"]
        }))
        result = assess_extra(outlook, "mushrooms", 100)
        self.assertTrue(result.available_now)
        self.assertTrue(result.preserves_reserve)
        self.assertEqual(result.decision, "needs_staff_review")
        self.assertIn(SAFETY_REVIEW_NOTE, result.staff_review_notes)


class FakeInventory:
    def __init__(self, status="ok", mismatch=False):
        self.status, self.mismatch = status, mismatch
        self.confirm_calls = 0
        self.seen_changes = []

    def requirements(self, dish_id, changes):
        self.seen_changes.append((dish_id, copy.deepcopy(changes)))
        return {changes["replacement"]: 200, "rice": 200}

    def check(self, dish_id, changes, qty=1):
        requirements = {i: amount * qty for i, amount in self.requirements(dish_id, changes).items()}
        if self.mismatch:
            requirements["rice"] += 1
        return {"status": self.status, "reasons": ["inventory check"], "requirements": requirements}

    def available(self, ingredient_id):
        return {"tofu": 1200, "mushrooms": 1300, "rice": 10000}[ingredient_id]

    def confirm(self, order):
        self.confirm_calls += 1
        return {"status": "ok", "reasons": [], "requirements": {}}


def connected(qty=1):
    raw = payload()
    for c in raw["candidates"]:
        c["dish_id"] = "bowl"
        c["changes"] = {"replacement": "mushrooms" if c["option_id"] == "mushroom_bowl" else "tofu"}
        c.pop("requirements")
    return ConnectedEvaluationRequest.model_validate({
        "evaluated_at": raw["evaluated_at"], "forecast": raw["forecast"],
        "usable_until": raw["forecast"]["end"], "candidates": raw["candidates"], "qty": qty,
    })


class InventoryTests(unittest.TestCase):
    def test_team_api_net_stock_is_not_deducted_twice_and_no_confirm(self):
        inventory = FakeInventory()
        gateway = InventoryGateway(inventory, success_statuses={"ok"}, blocked_statuses={"unavailable"})
        result = asyncio.run(evaluate_with_inventory(connected(), gateway))
        tofu = next(o for o in result.ranked_options if o.option_id == "tofu_bowl")
        self.assertEqual(tofu.tradeoffs[0].available_after_commitments, 1200)
        self.assertEqual(inventory.confirm_calls, 0)
        self.assertTrue(any("conservatively" in w for w in result.warnings))
        self.assertTrue(any("no authoritative" in w for w in result.warnings))
        self.assertEqual(inventory.seen_changes[0], ("bowl", {"replacement": "mushrooms"}))

    def test_known_commitments_reduce_forecast_demand_once(self):
        request = connected()
        request.committed_in_horizon = {"tofu": 500, "mushrooms": 0, "rice": 0}
        gateway = InventoryGateway(FakeInventory(), success_statuses={"ok"})
        result = asyncio.run(evaluate_with_inventory(request, gateway))
        tofu = next(o for o in result.ranked_options if o.option_id == "tofu_bowl").tradeoffs[0]
        self.assertEqual(tofu.available_after_commitments, 1200)
        self.assertAlmostEqual(tofu.uncommitted_expected_demand, 1002.1)

    def test_qty_applied_once_to_full_recipe(self):
        gateway = InventoryGateway(FakeInventory(), success_statuses={"ok"})
        result = asyncio.run(evaluate_with_inventory(connected(2), gateway))
        self.assertTrue(all(t.requested_quantity == 400 for o in result.ranked_options for t in o.tradeoffs))

    def test_requirements_disagreement_and_unknown_status_fail_closed(self):
        for inventory in [FakeInventory(mismatch=True), FakeInventory(status="not-recognized")]:
            result = asyncio.run(evaluate_with_inventory(
                connected(), InventoryGateway(inventory, success_statuses={"ok"}),
            ))
            self.assertEqual(result.status, "needs_staff_review")
            self.assertIsNone(result.recommended_option_id)
            self.assertEqual(inventory.confirm_calls, 0)

    def test_failed_check_cannot_be_overridden_by_stock(self):
        gateway = InventoryGateway(FakeInventory(status="unavailable"), success_statuses={"ok"},
                                   blocked_statuses={"unavailable"})
        result = asyncio.run(evaluate_with_inventory(connected(), gateway))
        self.assertEqual(result.status, "unavailable")

    def test_async_inventory_functions_supported(self):
        class AsyncInventory(FakeInventory):
            async def available(self, ingredient_id):
                return super().available(ingredient_id)
        result = asyncio.run(evaluate_with_inventory(
            connected(), InventoryGateway(AsyncInventory(), success_statuses={"ok"}),
        ))
        self.assertIsNotNone(result.recommended_option_id)



if __name__ == "__main__":
    unittest.main()
