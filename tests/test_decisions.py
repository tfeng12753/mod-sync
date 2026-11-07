import asyncio
import copy
import json
import unittest
from datetime import timedelta
from pathlib import Path

from backend.engine import InventoryGateway, ModificationRequest, decide_modification, decision_is_current


class Inventory:
    def __init__(self):
        self.stock = {"guacamole": 5000, "rice": 50000, "beans": 5000}
        self.confirm_calls = 0

    def requirements(self, dish_id, changes):
        return changes["recipe"]

    def check(self, dish_id, changes, qty=1):
        return {"status": "ok", "reasons": [],
                "requirements": {i: q * qty for i, q in changes["recipe"].items()}}

    def available(self, ingredient_id):
        return self.stock[ingredient_id]

    def confirm(self, order):
        self.confirm_calls += 1
        raise AssertionError("evaluation must not confirm")


def raw_request():
    raw = json.loads((Path(__file__).resolve().parents[1] / "fixtures/evaluation_request.json").read_text())
    forecast = raw["forecast"]
    forecast["model"] = "historical"
    for item in forecast["ingredients"] + forecast["history"]:
        item["ingredient_id"] = {"tofu": "guacamole", "mushrooms": "beans"}.get(item["ingredient_id"], item["ingredient_id"])
        if "quantity" in item and item["ingredient_id"] != "rice":
            item["quantity"] = 100
    return {
        "action": "extra", "dish_id": "bowl", "target_ingredient_id": "avocado",
        "original_changes": {"recipe": {"rice": 200}},
        "evaluation": {"evaluated_at": raw["evaluated_at"], "forecast": forecast,
            "usable_until": forecast["end"], "candidates": [{
                "option_id": "extra_guac", "label": "Extra guacamole instead of avocado",
                "dish_id": "bowl", "changes": {"recipe": {"rice": 200, "guacamole": 60}},
                "approved": True, "customer_constraints_met": True, "preparation_feasible": True,
                "similarity": .65, "similarity_source": "inventory",
                "ingredient_cost_change_cents": 0, "additional_labor_cents": 0, "price_change_cents": 999,
            }]},
        "catalog": [
            {"item_id": "guacamole", "in_inventory": True, "on_menu": True,
             "composition_verified": True, "contains_ingredients": ["avocado", "onion"], "can_serve_separately": True},
            {"item_id": "rice", "in_inventory": True, "composition_verified": True,
             "contains_ingredients": ["rice"], "can_serve_separately": True},
            {"item_id": "beans", "in_inventory": True, "composition_verified": True,
             "contains_ingredients": ["beans"], "can_serve_separately": True},
            {"item_id": "avocado", "on_menu": True, "composition_verified": True,
             "contains_ingredients": ["avocado"], "can_serve_separately": False},
        ],
        "offers": [{"option_id": "extra_guac", "offered_item_id": "guacamole", "approved_for_dish": True,
                    "portion_quantity": 60, "portion_unit": "g", "portion_requirements": {"guacamole": 60},
                    "menu_price_change_cents": 150, "menu_price_reference": "menu-extra-guac-v1"}],
    }


def decide(raw=None, inventory=None):
    inventory = inventory or Inventory()
    result = asyncio.run(decide_modification(
        ModificationRequest.model_validate(raw or raw_request()),
        InventoryGateway(inventory, success_statuses={"ok"}, blocked_statuses={"unavailable"}),
    ))
    assert inventory.confirm_calls == 0
    return result


class DecisionTests(unittest.TestCase):
    def test_surplus_can_lift_lower_similarity_above_combined_cutoff(self):
        result = decide()
        self.assertEqual(result.status, "reply_to_customer")
        option = result.customer_options[0]
        self.assertEqual(option.similarity, .65)
        self.assertAlmostEqual(option.ranking_score, .7575)
        self.assertEqual(option.price_change_cents, 150)  # menu, not candidate's 999
        self.assertTrue(option.requires_customer_acceptance)
        self.assertEqual(option.offered_item_id, "guacamole")

    def test_below_combined_cutoff_is_still_offered_with_a_note(self):
        raw = raw_request()
        raw["evaluation"]["candidates"][0]["similarity"] = .60
        result = decide(raw)
        self.assertEqual(result.unavailable_options, [])
        [option] = result.customer_options + result.staff_options
        self.assertLess(option.ranking_score, .75)
        self.assertTrue(any("below the 0.75" in r.lower() for r in option.reasons))

    def test_exact_cutoff_is_inclusive(self):
        raw = raw_request()
        raw["evaluation"]["candidates"][0]["similarity"] = .6
        raw["evaluation"]["policy"] = {"similarity_weight": .5, "reserve_weight": .4, "cost_weight": .1}
        result = decide(raw)
        self.assertEqual(result.customer_options[0].ranking_score, .75)

    def test_staff_note_routes_qualifying_option_to_staff(self):
        raw = raw_request()
        raw["evaluation"]["staff_review_notes"] = ["Person 2: staff must review."]
        result = decide(raw)
        self.assertEqual(result.status, "needs_staff_review")
        self.assertEqual(result.customer_options, [])
        self.assertIn("Person 2: staff must review.", result.staff_options[0].staff_review_notes)

    def test_inventory_similarity_is_required_for_alternative(self):
        raw = raw_request()
        raw["evaluation"]["candidates"][0]["similarity_source"] = "supplied"
        result = decide(raw)
        self.assertEqual(result.status, "unavailable")

    def test_unknown_catalog_item_is_not_recommended(self):
        raw = raw_request()
        raw["catalog"] = [c for c in raw["catalog"] if c["item_id"] != "guacamole"]
        self.assertEqual(decide(raw).status, "unavailable")

    def test_component_cannot_be_extracted_from_mixture(self):
        raw = raw_request()
        offer = raw["offers"][0]
        offer["offered_item_id"] = "avocado"  # only trapped inside guacamole
        self.assertEqual(decide(raw).status, "unavailable")

    def test_composite_extra_must_contain_requested_ingredient(self):
        raw = raw_request()
        raw["catalog"][0]["contains_ingredients"] = ["onion"]
        self.assertEqual(decide(raw).status, "unavailable")

    def test_extra_must_preserve_base_recipe_and_match_portion(self):
        raw = raw_request()
        raw["evaluation"]["candidates"][0]["changes"]["recipe"]["rice"] = 100
        self.assertEqual(decide(raw).status, "unavailable")

    def test_menu_price_and_extra_count_scaled_once(self):
        raw = raw_request()
        raw["evaluation"]["qty"] = 2
        raw["extra_portions_per_dish"] = 2
        raw["evaluation"]["candidates"][0]["changes"]["recipe"]["guacamole"] = 120
        option = decide(raw).customer_options[0]
        self.assertEqual(option.price_change_cents, 600)
        self.assertEqual(option.dish_quantity, 2)
        self.assertEqual(option.portions_per_dish, 2)
        self.assertEqual(option.total_portions, 4)
        self.assertEqual(option.total_offered_quantity, 240)
        self.assertEqual(option.requirements, {"rice": 400, "guacamole": 240})

    def test_unknown_extra_price_requires_staff_not_automatic_free(self):
        raw = raw_request()
        raw["offers"][0].pop("menu_price_change_cents")
        raw["offers"][0].pop("menu_price_reference")
        result = decide(raw)
        self.assertEqual(result.status, "needs_staff_review")
        self.assertIsNone(result.staff_options[0].price_change_cents)

    def test_restaurant_authorized_absorption_and_upcharge(self):
        raw = raw_request()
        raw["offers"][0].pop("menu_price_change_cents")
        raw["offers"][0].pop("menu_price_reference")
        raw["policy"] = {"pricing_policy_id": "approved-rule", "absorb_incremental_cost_up_to_cents": 50,
                         "approved_upcharge_cents": 150}
        candidate = raw["evaluation"]["candidates"][0]
        candidate["similarity"] = 1
        candidate["ingredient_cost_change_cents"] = 30
        self.assertEqual(decide(copy.deepcopy(raw)).customer_options[0].price_change_cents, 0)
        candidate["ingredient_cost_change_cents"] = 150
        self.assertEqual(decide(raw).customer_options[0].price_change_cents, 150)

    def test_raw_stock_failure_cannot_be_sent_as_staff_option(self):
        inventory = Inventory()
        inventory.stock["guacamole"] = 10
        raw = raw_request()
        raw["evaluation"]["staff_review_notes"] = ["Review"]
        self.assertEqual(decide(raw, inventory).status, "unavailable")

    def test_forecast_risk_routes_otherwise_qualifying_option_to_staff(self):
        inventory = Inventory()
        inventory.stock["guacamole"] = 160
        raw = raw_request()
        raw["evaluation"]["candidates"][0]["similarity"] = 1
        result = decide(raw, inventory)
        self.assertEqual(result.status, "needs_staff_review")
        self.assertTrue(any("forecast reserves" in n for n in result.staff_options[0].staff_review_notes))

    def test_cannot_remove_ingredient_from_unchanged_premix(self):
        raw = raw_request()
        raw["action"] = "omit"
        raw["original_changes"]["recipe"]["guacamole"] = 60
        raw["offers"] = [{"option_id": "extra_guac", "approved_for_dish": True}]
        result = decide(raw)
        self.assertEqual(result.status, "unavailable")
        self.assertTrue(any("still contains" in r for r in result.unavailable_options[0].reasons))

    def test_removing_whole_prepared_component_can_satisfy_omission(self):
        raw = raw_request()
        raw["action"] = "omit"
        raw["original_changes"]["recipe"]["guacamole"] = 60
        raw["evaluation"]["candidates"][0]["label"] = "No guacamole"
        raw["evaluation"]["candidates"][0]["changes"]["recipe"] = {"rice": 200}
        raw["offers"] = [{"option_id": "extra_guac", "approved_for_dish": True}]
        result = decide(raw)
        self.assertEqual(result.status, "reply_to_customer")
        self.assertEqual(result.customer_options[0].price_change_cents, 0)
        self.assertFalse(result.customer_options[0].similarity_required)

    def test_whole_component_replacement_without_excluded_ingredient(self):
        raw = raw_request()
        raw["action"] = "substitute"
        raw["original_changes"]["recipe"]["guacamole"] = 60
        raw["evaluation"]["candidates"][0]["changes"]["recipe"] = {"rice": 200, "beans": 60}
        raw["offers"][0].update(offered_item_id="beans", portion_requirements={"beans": 60})
        self.assertEqual(decide(raw).status, "reply_to_customer")

    def test_exact_extra_has_no_replacement_similarity_requirement(self):
        raw = raw_request()
        raw["target_ingredient_id"] = "guacamole"
        raw["evaluation"]["candidates"][0]["similarity"] = None
        result = decide(raw)
        self.assertEqual(result.status, "reply_to_customer")
        self.assertFalse(result.customer_options[0].similarity_required)

    def test_extra_cannot_be_a_noop_without_offered_item(self):
        raw = raw_request()
        raw["offers"] = [{"option_id": "extra_guac", "approved_for_dish": True,
                          "menu_price_change_cents": 0, "menu_price_reference": "test-menu"}]
        raw["evaluation"]["candidates"][0]["changes"]["recipe"] = {"rice": 200}
        self.assertEqual(decide(raw).status, "unavailable")

    def test_portion_cannot_be_mislabeled_as_requested_ingredient(self):
        raw = raw_request()
        raw["catalog"][-1]["can_serve_separately"] = True
        raw["offers"][0].update(offered_item_id="avocado", portion_requirements={"beans": 60})
        raw["evaluation"]["candidates"][0]["changes"]["recipe"] = {"rice": 200, "beans": 60}
        self.assertEqual(decide(raw).status, "unavailable")

    def test_allergy_note_is_mandatory_even_for_qualifying_composite_extra(self):
        raw = raw_request()
        raw["evaluation"]["restrictions"] = [{"kind": "allergy", "value": "soy",
            "staff_review_note": "Person 2: soy allergy requires staff review."}]
        raw["evaluation"]["ingredient_profiles"] = [
            {"ingredient_id": i, "unit": "g", "verified": True, "allergen_status": {"soy": False}}
            for i in ["guacamole", "rice"]
        ]
        result = decide(copy.deepcopy(raw))
        self.assertEqual(result.status, "needs_staff_review")
        self.assertTrue(any("cannot certify" in n for n in result.staff_options[0].staff_review_notes))
        raw["evaluation"]["ingredient_profiles"][0]["allergen_status"]["soy"] = True
        self.assertEqual(decide(raw).status, "unavailable")

    def test_rounding_cannot_lift_below_threshold_score_to_qualify(self):
        raw = raw_request()
        raw["evaluation"]["candidates"][0]["similarity"] = .5999998
        raw["evaluation"]["policy"] = {"similarity_weight": .5, "reserve_weight": .4, "cost_weight": .1}
        result = decide(raw)
        [option] = result.customer_options + result.staff_options
        self.assertLess(option.ranking_score, .75)  # rounding can't lift it over the line
        self.assertTrue(any("below the 0.75" in r.lower() for r in option.reasons))

    def test_recipe_changed_after_checks_is_never_a_staff_proposal(self):
        class ChangingInventory(Inventory):
            def __init__(self):
                super().__init__()
                self.proposal_reads = 0

            def requirements(self, dish_id, changes):
                result = dict(super().requirements(dish_id, changes))
                if "guacamole" in result:
                    self.proposal_reads += 1
                    if self.proposal_reads >= 2:  # final read after requirements + check
                        result["rice"] = 100
                return result
        raw = raw_request()
        raw["evaluation"]["staff_review_notes"] = ["Person 2: review this request."]
        result = decide(raw, ChangingInventory())
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(result.staff_options, [])
        self.assertTrue(any("recipe changed" in r.lower() for r in result.unavailable_options[0].reasons))

    def test_omission_cannot_remove_unrelated_ingredients(self):
        raw = raw_request()
        raw["action"] = "omit"
        raw["original_changes"]["recipe"].update(guacamole=60, beans=100)
        raw["evaluation"]["candidates"][0]["changes"]["recipe"] = {"rice": 200}
        raw["offers"] = [{"option_id": "extra_guac", "approved_for_dish": True}]
        self.assertEqual(decide(raw).status, "unavailable")

    def test_substitution_cannot_remove_unrelated_ingredients(self):
        raw = raw_request()
        raw["action"] = "substitute"
        raw["original_changes"]["recipe"].update(guacamole=60)
        raw["evaluation"]["candidates"][0]["changes"]["recipe"] = {"rice": 100, "beans": 60}
        raw["offers"][0].update(offered_item_id="beans", portion_requirements={"beans": 60})
        self.assertEqual(decide(raw).status, "unavailable")

    def test_explicit_swap_cannot_claim_unrelated_removal(self):
        raw = raw_request()
        raw["action"] = "substitute"
        raw["original_changes"]["recipe"].update(guacamole=60)
        raw["evaluation"]["candidates"][0]["changes"]["recipe"] = {"rice": 100, "beans": 60}
        raw["offers"][0].update(offered_item_id="beans", portion_requirements={"beans": 60},
                                removed_requirements={"guacamole": 60, "rice": 100})
        self.assertEqual(decide(raw).status, "unavailable")

    def test_displayed_quantity_cannot_overstate_actual_extra(self):
        raw = raw_request()
        raw["offers"][0]["portion_quantity"] = 100
        self.assertEqual(decide(raw).status, "unavailable")

    def test_no_silent_portion_unit_conversion(self):
        raw = raw_request()
        raw["offers"][0]["portion_unit"] = "ml"
        self.assertEqual(decide(raw).status, "unavailable")

    def test_recommendation_expiry_is_explicit_and_not_a_reservation(self):
        result = decide()
        self.assertEqual(result.expires_at, result.evaluated_at + timedelta(minutes=5))
        self.assertTrue(decision_is_current(result, result.evaluated_at))
        self.assertFalse(decision_is_current(result, result.expires_at))
        self.assertFalse(decision_is_current(result, result.evaluated_at - timedelta(seconds=1)))
        self.assertTrue(result.requires_inventory_recheck_on_confirmation)


if __name__ == "__main__":
    unittest.main()
