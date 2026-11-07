"""One test per row of the edge-case table in backend/inventory/plan.md, plus basics."""

import unittest

from .helpers import codes, fresh_inventory


class BasicCheckTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()

    def test_plain_dish_is_ok(self):
        r = self.inv.check("chicken_bowl")
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.requirements, {"rice": 200, "chicken": 150, "oil": 5, "garlic_sauce": 40,
                                          "avocado": 60, "pickled_onion": 25})
        self.assertEqual(r.price_delta_cents, 0)
        self.assertFalse(r.requires_staff_review)

    def test_remove_assembly_component_by_main_ingredient(self):
        r = self.inv.check("chicken_bowl", [{"op": "remove", "ingredient": "avocado"}])
        self.assertEqual(r.status, "ok")
        self.assertNotIn("avocado", r.requirements)
        self.assertIn("NO Sliced avocado", r.kitchen_instructions)

    def test_remove_whole_premade_component(self):
        r = self.inv.check("chicken_bowl", [{"op": "remove", "ingredient": "garlic_sauce"}])
        self.assertEqual(r.status, "ok")
        self.assertNotIn("garlic_sauce", r.requirements)

    def test_remove_non_main_ingredient_drops_only_that_usage(self):
        r = self.inv.check("tofu_bowl", [{"op": "remove", "ingredient": "cornstarch"}])
        self.assertEqual(r.status, "ok")
        self.assertNotIn("cornstarch", r.requirements)
        self.assertEqual(r.requirements["tofu"], 150)

    def test_extra_within_max_adds_price_and_stock(self):
        r = self.inv.check("chicken_bowl", [{"op": "add", "ingredient": "avocado", "qty": 2}])
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.requirements["avocado"], 180)
        self.assertEqual(r.price_delta_cents, 400)

    def test_extra_of_component_not_in_base_dish(self):
        r = self.inv.check("tofu_bowl", [{"op": "add", "ingredient": "chicken"}])
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.requirements["chicken"], 150)

    def test_approved_substitute(self):
        r = self.inv.check("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": "tofu"}])
        self.assertEqual(r.status, "ok")
        self.assertNotIn("chicken", r.requirements)
        self.assertEqual(r.requirements["tofu"], 150)
        self.assertIn("SUB Crispy tofu for Grilled chicken", r.kitchen_instructions)

    def test_substitute_price_delta(self):
        r = self.inv.check("chicken_bowl", [{"op": "substitute", "ingredient": "grilled_chicken", "with": "roasted_mushrooms"}])
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.price_delta_cents, -100)

    def test_on_side_whole_sauce(self):
        r = self.inv.check("chicken_bowl", [{"op": "on_side", "ingredient": "garlic_sauce"}])
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.requirements["garlic_sauce"], 40)
        self.assertIn("Garlic sauce ON THE SIDE", r.kitchen_instructions)

    def test_less_halves_rounding_up(self):
        r = self.inv.check("chicken_bowl", [{"op": "less", "ingredient": "pickled_onion"}])
        self.assertEqual(r.status, "ok")
        self.assertEqual(r.requirements["pickled_onion"], 13)  # 25 / 2 rounded up


class EdgeCaseTableTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()

    def test_unknown_id(self):
        r = self.inv.check("chicken_bowl", [{"op": "add", "ingredient": "bacon"}])
        self.assertEqual(r.status, "needs_clarification")
        self.assertEqual(codes(r), ["UNKNOWN_ITEM"])

    def test_unknown_op(self):
        r = self.inv.check("chicken_bowl", [{"op": "deep_fry", "ingredient": "avocado"}])
        self.assertEqual(r.status, "needs_clarification")
        self.assertEqual(codes(r), ["UNKNOWN_OP"])

    def test_unknown_dish(self):
        r = self.inv.check("pizza", [])
        self.assertEqual(r.status, "needs_clarification")
        self.assertEqual(codes(r), ["UNKNOWN_DISH"])

    def test_conflicting_ops(self):
        r = self.inv.check("chicken_bowl", [{"op": "remove", "ingredient": "avocado"},
                                            {"op": "add", "ingredient": "sliced_avocado"}])
        self.assertEqual(r.status, "needs_clarification")
        self.assertEqual(codes(r), ["CONFLICTING_OPS"])
        self.assertIn("avocado", r.requirements)  # neither op applied

    def test_substitute_without_target(self):
        r = self.inv.check("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": None}])
        self.assertEqual(r.status, "needs_clarification")
        self.assertEqual(codes(r), ["SUBSTITUTE_TARGET_MISSING"])

    def test_remove_ingredient_inside_premade_is_blocked(self):
        r = self.inv.check("chicken_bowl", [{"op": "remove", "ingredient": "garlic"}])
        self.assertEqual(r.status, "blocked")
        self.assertEqual(codes(r), ["PREMADE_INGREDIENT"])
        self.assertIn("Sesame dressing", r.reasons[0].message)  # suggests the approved swap

    def test_remove_something_not_in_dish_is_ok_noop(self):
        r = self.inv.check("chicken_bowl", [{"op": "remove", "ingredient": "peanuts"}])
        self.assertEqual(r.status, "ok")
        self.assertEqual(codes(r), ["NOT_IN_DISH"])
        self.assertEqual(r.reasons[0].status, "info")

    def test_extra_not_offered(self):
        r = self.inv.check("chicken_bowl", [{"op": "add", "ingredient": "tomato"}])
        self.assertEqual(r.status, "blocked")
        self.assertEqual(codes(r), ["EXTRA_NOT_OFFERED"])

    def test_extra_over_max(self):
        r = self.inv.check("chicken_bowl", [{"op": "add", "ingredient": "avocado", "qty": 3}])
        self.assertEqual(r.status, "blocked")
        self.assertEqual(codes(r), ["EXTRA_OVER_MAX"])

    def test_unapproved_swap(self):
        r = self.inv.check("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": "falafel"}])
        self.assertEqual(r.status, "blocked")
        self.assertEqual(codes(r), ["SUBSTITUTE_NOT_APPROVED"])

    def test_excluded_allergen_inside_premade_is_blocked_with_source(self):
        r = self.inv.check("satay_noodles", [{"op": "remove", "ingredient": "peanuts"}], exclude_allergens=["Peanut"])
        self.assertEqual(r.status, "blocked")
        self.assertEqual(codes(r), ["ALLERGEN_PRESENT"])
        self.assertIn("Peanut butter (in Peanut sauce)", r.reasons[0].message)
        self.assertTrue(r.requires_staff_review)

    def test_allergen_ok_after_fix_still_needs_review_and_has_protocol(self):
        r = self.inv.check("satay_noodles",
                           [{"op": "remove", "ingredient": "peanuts"},
                            {"op": "substitute", "ingredient": "peanut_sauce", "with": "sesame_dressing"}],
                           exclude_allergens=["peanut"])
        self.assertEqual(r.status, "ok")
        self.assertTrue(r.requires_staff_review)
        self.assertEqual(r.kitchen_instructions[0], "ALLERGY: peanut")
        self.assertIn("allergy_protocol", r.evidence)
        self.assertTrue(any("shared wok" in c for c in r.evidence["cross_contact"]))
        self.assertNotIn("peanut", r.evidence["allergens_present"])

    def test_diet_violation(self):
        r = self.inv.check("miso_soup", require_diet=["vegan"])
        self.assertEqual(r.status, "blocked")
        self.assertEqual(codes(r), ["DIET_CONFLICT"])
        self.assertIn("Bonito flakes", r.reasons[0].message)

    def test_diet_satisfied(self):
        r = self.inv.check("tofu_bowl", require_diet=["vegan"])
        self.assertEqual(r.status, "ok")
        self.assertTrue(r.requires_staff_review)

    def test_out_of_stock(self):
        self.inv.set_on_hand("avocado", 30, "test")
        r = self.inv.check("chicken_bowl")
        self.assertEqual(r.status, "out_of_stock")
        self.assertEqual(codes(r), ["OUT_OF_STOCK"])

    def test_safety_buffer_counts_as_short(self):
        self.inv.set_on_hand("avocado", 100, "test")  # buffer is 60; 100 - 60 < 60 needed
        r = self.inv.check("chicken_bowl")
        self.assertEqual(r.status, "out_of_stock")
        self.assertEqual(codes(r), ["LOW_STOCK"])

    def test_holds_reduce_availability_for_checks(self):
        self.inv.set_on_hand("falafel", 8, "test")
        self.assertEqual(self.inv.hold("o1", {"falafel": 4}).code, "HELD")
        r = self.inv.check("falafel_wrap")
        self.assertEqual(r.status, "out_of_stock")  # 4 left, buffer 4

    def test_qty_multiplies_requirements(self):
        r = self.inv.check("chicken_bowl", [{"op": "add", "ingredient": "avocado"}], qty=3)
        self.assertEqual(r.requirements["avocado"], 360)
        self.assertEqual(r.requirements["rice"], 600)
        self.assertEqual(r.price_delta_cents, 600)

    def test_invalid_qty(self):
        r = self.inv.check("chicken_bowl", qty=0)
        self.assertEqual(r.status, "needs_clarification")
        self.assertEqual(codes(r), ["INVALID_QTY"])

    def test_on_side_of_ingredient_inside_premade_is_blocked(self):
        r = self.inv.check("chicken_bowl", [{"op": "on_side", "ingredient": "garlic"}])
        self.assertEqual(r.status, "blocked")

    def test_status_precedence_keeps_all_reasons(self):
        self.inv.set_on_hand("avocado", 0, "test")
        r = self.inv.check("chicken_bowl", [{"op": "remove", "ingredient": "garlic"},
                                            {"op": "add", "ingredient": "bacon"}])
        self.assertEqual(r.status, "needs_clarification")
        self.assertEqual(set(codes(r)), {"UNKNOWN_ITEM", "PREMADE_INGREDIENT", "OUT_OF_STOCK"})


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()

    def test_candidates_sorted_by_similarity_with_chef_scores(self):
        cands = self.inv.substitute_candidates("chicken_bowl", "chicken")
        self.assertEqual([c.with_ for c in cands], ["crispy_tofu", "roasted_mushrooms"])
        self.assertEqual([c.similarity_source for c in cands], ["chef", "chef"])
        self.assertTrue(all(c.feasible for c in cands))

    def test_tag_similarity_fallback(self):
        cands = self.inv.substitute_candidates("chicken_bowl", "rice_base")
        self.assertEqual(len(cands), 1)
        self.assertEqual(cands[0].similarity_source, "tags")
        self.assertTrue(1 <= cands[0].similarity <= 5)

    def test_infeasible_candidates_filtered(self):
        self.inv.set_on_hand("tofu", 0, "test")
        cands = self.inv.substitute_candidates("chicken_bowl", "chicken")
        self.assertEqual([c.with_ for c in cands], ["roasted_mushrooms"])
        all_cands = self.inv.substitute_candidates("chicken_bowl", "chicken", feasible_only=False)
        self.assertEqual(len(all_cands), 2)

    def test_allergy_candidates_keep_other_ops_and_exclusions(self):
        cands = self.inv.substitute_candidates(
            "satay_noodles", "peanut_sauce", exclude_allergens=["peanut"],
            base_ops=[{"op": "remove", "ingredient": "peanuts"},
                      {"op": "substitute", "ingredient": "peanut_sauce", "with": None}])
        self.assertEqual([c.with_ for c in cands], ["sesame_dressing", "garlic_sauce"])
        self.assertTrue(all(c.check.requires_staff_review for c in cands))
        self.assertTrue(all("peanuts" not in c.requirements for c in cands))

    def test_allergen_excludes_candidate(self):
        cands = self.inv.substitute_candidates("satay_noodles", "peanut_sauce",
                                               exclude_allergens=["peanut", "sesame"],
                                               base_ops=[{"op": "remove", "ingredient": "peanuts"}])
        self.assertEqual([c.with_ for c in cands], ["garlic_sauce"])

    def test_ingredient_target_inside_premade_finds_component_swaps(self):
        cands = self.inv.substitute_candidates("chicken_bowl", "garlic")
        self.assertEqual([c.with_ for c in cands], ["sesame_dressing"])

    def test_no_approved_swaps_returns_empty(self):
        self.assertEqual(self.inv.substitute_candidates("miso_soup", "tofu"), [])

    def test_candidate_serializes_for_forecast(self):
        c = self.inv.substitute_candidates("chicken_bowl", "chicken")[0].to_dict()
        for key in ("dish", "replace", "with", "similarity", "price_delta_cents", "cost_delta_cents",
                    "requirements", "available", "check"):
            self.assertIn(key, c)


if __name__ == "__main__":
    unittest.main()
