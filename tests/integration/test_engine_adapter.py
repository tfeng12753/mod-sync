"""Inventory -> forecast engine adapter: demo scenarios, allergy flow, gateway shapes, read-only."""

import asyncio
import unittest
from dataclasses import replace
from datetime import timedelta

from backend.engine import ForecastRequest, decision_is_current
from backend.engine.constraints import SAFETY_REVIEW_NOTE
from backend.engine.decisions import ModificationDecision
from backend.integration import (
    DEMO_EVALUATION_TIME,
    build_modification_requests,
    inventory_functions,
    make_changes,
    make_gateway,
    route_options,
)
from backend.integration.engine_adapter import BLOCKED_STATUSES, SUCCESS_STATUSES
from tests.inventory.helpers import fresh_inventory

SWAP_CHICKEN = [{"op": "substitute", "ingredient": "chicken", "with": None}]


def request(dish, ops=(), qty=1, text="test", **flags):
    return {"dish": dish, "qty": qty, "ops": list(ops), "original_text": text, "flags": flags or None}


def route(inv, parser_output, **kwargs):
    evaluation = inv.evaluate_request(parser_output)
    return evaluation, asyncio.run(route_options(inv, evaluation, **kwargs))


def changes_of(evaluation, choice_or_index):
    index = choice_or_index if isinstance(choice_or_index, int) else choice_or_index.option_index
    return [(c["replace"], c["with"]) for c in evaluation.options[index].changes]


class GatewayFunctionTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()
        self.fn = inventory_functions(self.inv)

    def test_requirements_are_per_dish(self):
        ops = [{"op": "less", "ingredient": "garlic_sauce"}]
        one = self.fn.requirements("chicken_bowl", make_changes(ops))
        self.assertEqual(one["garlic_sauce"], 20)
        three = self.fn.requirements("chicken_bowl", make_changes(ops, qty=3))
        # ceil(20 * 3) / 3 per dish: agrees exactly with check(qty=3)
        self.assertEqual(three["garlic_sauce"] * 3, self.inv.check("chicken_bowl", ops, 3).requirements["garlic_sauce"])

    def test_changes_may_be_a_plain_ops_list(self):
        ops = [{"op": "remove", "ingredient": "avocado"}]
        self.assertEqual(self.fn.requirements("chicken_bowl", ops), self.inv.requirements("chicken_bowl", ops))
        with self.assertRaises(TypeError):
            self.fn.requirements("chicken_bowl", "no avocado")
        with self.assertRaises(ValueError):
            self.fn.requirements("chicken_bowl", {"ops": ops, "qty": 0})

    def test_check_shape(self):
        result = self.fn.check("chicken_bowl", make_changes([{"op": "remove", "ingredient": "garlic"}]), qty=2)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["reasons"])
        self.assertTrue(all(isinstance(r, str) for r in result["reasons"]))
        self.assertIn("made ahead", result["reasons"][0])
        self.assertEqual(result["requirements"]["rice"], 400)

    def test_check_applies_allergy_flags_from_changes(self):
        changes = make_changes([], exclude_allergens=["peanut"])
        result = self.fn.check("satay_noodles", changes)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(result["requires_staff_review"])

    def test_gateway_status_mapping(self):
        gateway = make_gateway(self.inv)
        self.assertEqual(gateway.success_statuses, SUCCESS_STATUSES)
        self.assertEqual(gateway.blocked_statuses, BLOCKED_STATUSES)
        ok = asyncio.run(gateway.check("chicken_bowl", make_changes([]), qty=1))
        self.assertIs(ok[0], True)
        blocked = asyncio.run(gateway.check("chicken_bowl", make_changes([{"op": "remove", "ingredient": "garlic"}])))
        self.assertIs(blocked[0], False)
        self.set_tofu(0)
        short = asyncio.run(gateway.check("chicken_bowl", make_changes(
            [{"op": "substitute", "ingredient": "chicken", "with": "tofu"}])))
        self.assertIs(short[0], False)
        self.assertIn("Inventory status: out_of_stock.", short[1])

    def set_tofu(self, qty):
        self.inv.set_on_hand("tofu", qty, "test")

    def test_available_excludes_holds_and_never_negative(self):
        self.inv.hold("o-1", {"tofu": 300})
        self.assertEqual(self.fn.available("tofu"), 600)
        self.set_tofu(100)
        self.assertEqual(self.fn.available("tofu"), 0)

    def test_confirm_maps_to_inventory_confirm(self):
        out = self.fn.confirm({"order_id": "o-9", "requirements": {"tofu": 150}})
        self.assertTrue(out["ok"])
        self.assertEqual(self.inv.stock.on_hand("tofu"), 750)
        again = asyncio.run(make_gateway(self.inv).confirm({"order_id": "o-9", "requirements": {"tofu": 150}}))
        self.assertTrue(again["already_confirmed"])
        self.assertEqual(self.inv.stock.on_hand("tofu"), 750)
        with self.assertRaises(ValueError):
            self.fn.confirm({"requirements": {}})


class MappingTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()

    def test_chicken_swap_offers_carry_component_level_removals(self):
        evaluation = self.inv.evaluate_request(request("chicken_bowl", SWAP_CHICKEN))
        (req, indexes), (omit, omit_indexes) = build_modification_requests(self.inv, evaluation)
        self.assertEqual(indexes, [0, 1])
        self.assertEqual((omit.action, omit_indexes), ("omit", [2]))  # "leave the chicken off" is its own group
        self.assertEqual((req.action, req.target_ingredient_id, req.exclude_target), ("substitute", "chicken", True))
        tofu, mush = req.offers
        self.assertEqual(tofu.offered_item_id, "crispy_tofu")
        self.assertEqual(tofu.removed_requirements, {"chicken": 150, "oil": 5})
        self.assertEqual(tofu.portion_requirements, {"tofu": 150, "cornstarch": 10, "oil": 10})
        self.assertTrue(tofu.removed_requirements_verified)
        self.assertEqual((tofu.portion_quantity, tofu.portion_unit), (150, "g"))
        self.assertEqual(mush.menu_price_change_cents, 0)
        self.assertEqual(mush.menu_price_reference, "catalog:chicken_bowl:grilled_chicken->roasted_mushrooms")
        candidates = req.evaluation.candidates
        self.assertEqual([c.similarity for c in candidates], [0.8, 0.65])
        self.assertEqual({c.similarity_source for c in candidates}, {"inventory"})
        self.assertEqual(candidates[0].changes["ops"], evaluation.options[0].ops)

    def test_forecast_window_and_history(self):
        evaluation = self.inv.evaluate_request(request("chicken_bowl", SWAP_CHICKEN))
        req = build_modification_requests(self.inv, evaluation)[0][0]
        forecast = req.evaluation.forecast
        self.assertEqual(forecast.start, DEMO_EVALUATION_TIME)
        self.assertEqual(forecast.end, DEMO_EVALUATION_TIME + timedelta(hours=3))
        self.assertEqual(req.evaluation.usable_until, forecast.end)
        # Every item in any candidate recipe, plus the original recipe (chicken).
        self.assertEqual({i.ingredient_id for i in forecast.ingredients},
                         {i for o in evaluation.options for i in o.requirements} | {"chicken"})
        self.assertTrue(forecast.history)
        self.assertEqual(req.evaluation.restrictions, [])
        self.assertEqual(req.evaluation.ingredient_profiles, [])

    def test_whole_synthetic_history_validates_as_engine_forecast_request(self):
        items = self.inv.stocked_items()
        ForecastRequest(start=DEMO_EVALUATION_TIME, end=DEMO_EVALUATION_TIME + timedelta(hours=3),
                        time_zone="America/New_York", daypart="dinner",
                        ingredients=[{"ingredient_id": i, "unit": self.inv.unit_of(i)} for i in items],
                        history=self.inv.usage_history())

    def test_leave_off_option_maps_to_omit(self):
        evaluation = self.inv.evaluate_request(request("chicken_bowl", level="allergy", allergens=["egg"]))
        groups = build_modification_requests(self.inv, evaluation)
        self.assertEqual([(r.action, r.target_ingredient_id, ix) for r, ix in groups],
                         [("substitute", "garlic_sauce", [0]), ("omit", "garlic_sauce", [1])])
        omit = groups[1][0]
        self.assertIsNone(omit.offers[0].offered_item_id)
        self.assertIsNone(omit.offers[0].menu_price_change_cents)
        self.assertEqual(omit.evaluation.candidates[0].similarity_source, "not_applicable")

    def test_allergy_options_group_by_action_and_target(self):
        evaluation = self.inv.evaluate_request(request("satay_noodles", level="allergy", allergens=["peanut"]))
        groups = build_modification_requests(self.inv, evaluation)
        self.assertEqual([(r.action, r.target_ingredient_id, ix) for r, ix in groups],
                         [("substitute", "peanut_sauce", [0, 1]), ("omit", "peanuts", [2])])
        sub = groups[0][0]
        sesame = sub.offers[0]
        self.assertEqual(sesame.offered_item_id, "sesame_dressing")
        self.assertEqual(sesame.removed_requirements, {"peanut_sauce": 60, "peanuts": 15})
        self.assertEqual(sesame.portion_requirements, {"sesame_dressing": 40})
        self.assertEqual((sesame.portion_quantity, sesame.portion_unit), (40, "ml"))
        restrictions = sub.evaluation.restrictions
        self.assertEqual([(r.kind, r.value) for r in restrictions], [("allergy", "peanut")])
        profiles = {p.ingredient_id: p for p in sub.evaluation.ingredient_profiles}
        self.assertTrue(all(p.verified for p in profiles.values()))
        self.assertIs(profiles["peanut_sauce"].allergen_status["peanut"], True)  # premade: from its contents
        self.assertIs(profiles["sesame_dressing"].allergen_status["peanut"], False)
        self.assertEqual(sub.evaluation.candidates[0].changes["exclude_allergens"], ["peanut"])


class DemoScenarioTests(unittest.TestCase):
    """The four demo scenarios of docs/project-idea.md, end to end through the engine."""

    def setUp(self):
        self.inv = fresh_inventory()

    def test_1_close_substitute_as_asked(self):
        ev, r = route(self.inv, request("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": "tofu"}]))
        self.assertEqual(ev.status, "ready")
        self.assertEqual(r.status, "reply_to_customer")
        [choice] = r.choices
        self.assertIsNone(choice.option_index)
        self.assertEqual((choice.route, choice.score, choice.price_change_cents), ("customer", None, 0))
        # The forecast still tells staff/UI that tofu is tight tonight (informational).
        self.assertTrue(any("Firm tofu" in x and "reserve at risk" in x for x in choice.reasons))
        self.assertEqual(r.forecast_summary["tofu"]["risk"], "reserve_at_risk")
        self.assertEqual(r.expires_at, DEMO_EVALUATION_TIME + timedelta(minutes=5))

    def test_2_garlic_in_premade_sauce_offers_sesame_dressing(self):
        ev, r = route(self.inv, request("chicken_bowl", [{"op": "remove", "ingredient": "garlic"}]))
        self.assertEqual(ev.check.reasons[0].code, "PREMADE_INGREDIENT")
        self.assertEqual(r.status, "reply_to_customer")
        first, second = r.choices
        self.assertEqual(changes_of(ev, first), [("garlic_sauce", "sesame_dressing")])
        self.assertEqual(first.route, "customer")
        self.assertGreaterEqual(first.score, 0.75)
        self.assertEqual(first.similarity, 0.7)
        self.assertEqual(changes_of(ev, second), [("garlic_sauce", None)])
        self.assertIsNone(second.score)  # direct omission: no similarity question
        self.assertEqual(second.price_change_cents, 0)
        self.assertIn("Sesame dressing instead of Garlic sauce", r.customer_message)

    def test_3_forecast_makes_mushrooms_beat_tofu(self):
        ev, r = route(self.inv, request("chicken_bowl", SWAP_CHICKEN))
        # Inventory alone ranks by culinary similarity: tofu (0.8) before mushrooms (0.65).
        self.assertEqual(ev.recommended, 0)
        self.assertEqual(changes_of(ev, 0), [("grilled_chicken", "crispy_tofu")])
        # The engine sees tofu is needed for dinner and mushrooms are surplus.
        self.assertEqual(r.status, "reply_to_customer")
        best, tofu = r.choices[0], next(c for c in r.choices if c.option_index == 0)
        self.assertEqual(changes_of(ev, best), [("grilled_chicken", "roasted_mushrooms")])
        self.assertEqual(best.route, "customer")
        self.assertGreaterEqual(best.score, 0.75)
        self.assertEqual(best.price_change_cents, 0)
        # Tofu is in stock, so it is still offered: ranked after mushrooms, never hidden.
        self.assertEqual(r.unavailable, [])
        self.assertLess(tofu.score, 0.75)
        self.assertGreater(r.choices.index(tofu), 0)
        self.assertTrue(any("below the 0.75" in x.lower() for x in tofu.reasons))
        raw = r.raw[0]
        tofu_factors = next(o for o in raw["customer_options"] + raw["staff_options"]
                            if o["ranking_score"] is not None and o["ranking_score"] < 0.75)["ranking_factors"]
        self.assertLess(tofu_factors["reserve"], 0.5)  # forecast reserve deficit
        self.assertTrue(decision_is_current(ModificationDecision.model_validate(r.raw[0]), DEMO_EVALUATION_TIME))

    def test_3b_without_tofu_pressure_tofu_wins_again(self):
        # Same request, but a history where tofu is rarely used: the ranking flips back to tofu.
        history = [dict(rec, quantity=rec["quantity"] // 10) if rec["ingredient_id"] == "tofu" else rec
                   for rec in self.inv.usage_history()]
        ev, r = route(self.inv, request("chicken_bowl", SWAP_CHICKEN), history=history)
        self.assertEqual([changes_of(ev, c)[0][1] for c in r.choices], ["crispy_tofu", "roasted_mushrooms", None])
        self.assertGreater(r.choices[0].score, r.choices[1].score)
        self.assertEqual({c.route for c in r.choices}, {"customer"})
        self.assertIsNone(r.choices[-1].score)  # leaving it off is offered last, never ranked as a swap

    def test_4_stock_change_requires_reevaluation(self):
        ev, r = route(self.inv, request("chicken_bowl", SWAP_CHICKEN))
        mushrooms = ev.options[r.choices[0].option_index]
        # Staff drop the mushroom tray before the customer accepts: the cached result is stale.
        self.inv.set_on_hand("mushrooms", 100, "dropped a tray")
        stale = self.inv.confirm("order-4", mushrooms.requirements)
        self.assertFalse(stale.ok)
        self.assertEqual(stale.code, "INSUFFICIENT_STOCK")
        ev2, r2 = route(self.inv, request("chicken_bowl", SWAP_CHICKEN))
        self.assertEqual([changes_of(ev2, i) for i in range(len(ev2.options))],
                         [[("grilled_chicken", "crispy_tofu")], [("grilled_chicken", None)]])
        # Tofu is below the cutoff tonight but still offered, and recommended ahead of leaving it off.
        self.assertEqual([changes_of(ev2, c) for c in r2.choices],
                         [[("grilled_chicken", "crispy_tofu")], [("grilled_chicken", None)]])
        self.assertLess(r2.choices[0].score, 0.75)
        # A tofu delivery arrives: reevaluating now recommends tofu.
        self.inv.adjust("tofu", 2000, "delivery")
        ev3, r3 = route(self.inv, request("chicken_bowl", SWAP_CHICKEN))
        self.assertEqual(r3.status, "reply_to_customer")
        self.assertEqual(changes_of(ev3, r3.choices[0]), [("grilled_chicken", "crispy_tofu")])

    def test_decisions_expire(self):
        _, r = route(self.inv, request("chicken_bowl", SWAP_CHICKEN))
        decision = ModificationDecision.model_validate(r.raw[0])
        self.assertEqual(r.expires_at, DEMO_EVALUATION_TIME + timedelta(minutes=5))
        self.assertFalse(decision_is_current(decision, DEMO_EVALUATION_TIME + timedelta(minutes=5)))

    def test_multiple_dishes_do_not_gain_a_swap_discount(self):
        _, r = route(self.inv, request("chicken_bowl", SWAP_CHICKEN, qty=2))
        self.assertEqual(r.choices[0].price_change_cents, 0)
        self.assertEqual(r.raw[0]["customer_options"][0]["price_change_cents"], 0)

    def test_suggested_swap_keeps_full_extra_price_across_dishes(self):
        self.inv.set_on_hand("avocado", 5000, "plenty of reserve for pricing regression")
        dish = self.inv.catalog.dishes["chicken_bowl"]
        # Keep this pricing regression eligible despite the extra's added ingredient cost.
        self.inv.catalog.dishes[dish.id] = replace(dish, substitutions=tuple(
            replace(swap, similarity=0.8) if swap.with_ == "roasted_mushrooms" else swap
            for swap in dish.substitutions
        ))
        _, r = route(self.inv, request("chicken_bowl", SWAP_CHICKEN + [
            {"op": "add", "ingredient": "avocado"},
        ], qty=2))
        self.assertTrue(r.choices)
        self.assertEqual(r.choices[0].price_change_cents, 400)

    def test_extra_within_forecast_reserve_goes_to_customer(self):
        _, r = route(self.inv, request("chicken_bowl", [{"op": "add", "ingredient": "avocado"}]))
        self.assertEqual(r.status, "reply_to_customer")
        self.assertEqual(r.choices[0].price_change_cents, 200)
        self.assertTrue(any(x.startswith("Extra Avocado") for x in r.choices[0].reasons))

    def test_extra_that_eats_forecast_reserve_goes_to_staff(self):
        _, r = route(self.inv, request("tofu_bowl", [{"op": "add", "ingredient": "crispy_tofu"}]))
        self.assertEqual(r.status, "needs_staff_review")
        self.assertEqual(r.choices[0].route, "staff")
        self.assertTrue(any("Firm tofu" in n for n in r.choices[0].staff_notes))

    def test_unfixable_request_is_unavailable(self):
        ev, r = route(self.inv, request("chicken_bowl", [{"op": "add", "ingredient": "tomato"}]))
        self.assertEqual(ev.status, "unavailable")
        self.assertEqual((r.status, r.choices), ("unavailable", []))
        self.assertIn("isn't offered", r.customer_message)


class AllergyFlowTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()
        self.ev, self.r = route(self.inv, request("satay_noodles", text="my kid is allergic to peanuts",
                                                  level="allergy", allergens=["peanut"]))

    def test_routes_to_staff_with_recommendation(self):
        self.assertEqual(self.ev.status, "needs_staff_review")
        self.assertEqual(self.r.status, "needs_staff_review")
        self.assertTrue(self.r.choices)
        self.assertEqual({c.route for c in self.r.choices}, {"staff"})
        best = self.r.choices[0]
        self.assertEqual(changes_of(self.ev, best), [("crushed_peanuts", None), ("peanut_sauce", "sesame_dressing")])
        self.assertEqual(best.label, "Leave off Crushed peanuts; Sesame dressing instead of Peanut sauce")
        self.assertGreaterEqual(best.score, 0.75)
        # Leaving the sauce off is the customer's choice too, offered after both swaps.
        self.assertEqual(changes_of(self.ev, self.r.choices[-1]), [("crushed_peanuts", None), ("peanut_sauce", None)])
        self.assertIn("confirming", self.r.customer_message)

    def test_weak_substitute_is_offered_after_the_strong_one(self):
        labels = [changes_of(self.ev, c)[1] for c in self.r.choices]
        self.assertLess(labels.index(("peanut_sauce", "sesame_dressing")), labels.index(("peanut_sauce", "garlic_sauce")))
        garlic = next(c for c in self.r.choices if changes_of(self.ev, c)[1] == ("peanut_sauce", "garlic_sauce"))
        self.assertLess(garlic.score, 0.75)
        self.assertTrue(any("below the 0.75" in x.lower() for x in garlic.reasons))

    def test_staff_notes_are_preserved(self):
        self.assertIn(SAFETY_REVIEW_NOTE, self.r.staff_review_notes)
        allergy_note = next(n for n in self.r.staff_review_notes if "allergy: peanut" in n)
        self.assertIn("my kid is allergic to peanuts", allergy_note)
        for choice in self.r.choices:
            self.assertIn(SAFETY_REVIEW_NOTE, choice.staff_notes)
            self.assertIn(allergy_note, choice.staff_notes)

    def test_no_peanut_in_any_routed_recipe(self):
        for decision in self.r.raw:
            for option in decision["customer_options"] + decision["staff_options"]:
                self.assertNotIn("peanuts", option["requirements"])
                self.assertNotIn("peanut_sauce", option["requirements"])

    def test_engine_restriction_check_backs_up_inventory(self):
        # A tampered option that still contains peanuts: the engine re-checks it through the
        # gateway and its restriction check (catalog-generated profiles) flags the violation.
        evaluation = self.inv.evaluate_request(request("satay_noodles", level="allergy", allergens=["peanut"]))
        evaluation.options[0].ops = [op for op in evaluation.options[0].ops if op["ingredient"] != "crushed_peanuts"]
        evaluation.options[0].requirements = self.inv.check("satay_noodles", evaluation.options[0].ops).requirements
        r = asyncio.run(route_options(self.inv, evaluation))
        [blocked] = [u for u in r.unavailable if u["option_index"] == 0]
        self.assertTrue(any("restriction violated" in x for x in blocked["reasons"]))
        self.assertNotIn(0, [c.option_index for c in r.choices])

    def test_unspecified_allergy_flag_still_goes_to_staff_with_note(self):
        _, r = route(self.inv, request("tofu_bowl", text="I have an allergy", level="allergy"))
        self.assertEqual(r.status, "needs_staff_review")
        self.assertEqual(r.choices[0].route, "staff")
        self.assertTrue(any("allergy flagged but not specified" in n for n in r.choices[0].staff_notes))

    def test_diet_request_feasible_as_asked_goes_to_staff(self):
        _, r = route(self.inv, request("tofu_bowl", level="diet", diets=["vegan"]))
        self.assertEqual((r.status, r.choices[0].route), ("needs_staff_review", "staff"))
        self.assertIn(SAFETY_REVIEW_NOTE, r.staff_review_notes)


class ReadOnlyTests(unittest.TestCase):
    def test_routing_never_confirms_or_holds(self):
        inv = fresh_inventory()
        calls = []
        for name in ("confirm", "hold", "release", "adjust", "set_on_hand"):
            original = getattr(inv, name)
            setattr(inv, name, lambda *a, _n=name, _o=original, **k: calls.append(_n) or _o(*a, **k))
        before = {i: inv.stock.on_hand(i) for i in inv.stocked_items()}
        for parser_output in [
            request("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": "tofu"}]),
            request("chicken_bowl", [{"op": "remove", "ingredient": "garlic"}]),
            request("chicken_bowl", SWAP_CHICKEN),
            request("chicken_bowl", [{"op": "add", "ingredient": "avocado"}]),
            request("satay_noodles", level="allergy", allergens=["peanut"]),
        ]:
            route(inv, parser_output)
        self.assertEqual(calls, [])
        self.assertEqual({i: inv.stock.on_hand(i) for i in inv.stocked_items()}, before)
        self.assertEqual(inv.stock.held_orders(), [])


if __name__ == "__main__":
    unittest.main()
