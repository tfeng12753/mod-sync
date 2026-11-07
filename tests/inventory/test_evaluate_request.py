import json
import unittest

from .helpers import fresh_inventory


def changes(option):
    return [(c["replace"], c["with"]) for c in option.changes]


class EvaluateRequestTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()

    def ev(self, dish, ops=(), **flags):
        return self.inv.evaluate_request({"dish": dish, "ops": list(ops), "flags": flags or None,
                                          "original_text": "test"})

    # ready / approval paths

    def test_feasible_request_is_ready(self):
        e = self.ev("chicken_bowl", [{"op": "remove", "ingredient": "avocado"}])
        self.assertEqual(e.status, "ready")
        self.assertEqual(e.options, [])
        self.assertIsNone(e.recommended)
        self.assertEqual(e.requirements, e.check.requirements)
        self.assertNotIn("avocado", e.requirements)

    def test_unspecified_swap_offers_ranked_options(self):
        e = self.ev("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": None}])
        self.assertEqual(e.status, "needs_customer_approval")
        self.assertEqual([changes(o) for o in e.options],
                         [[("grilled_chicken", "crispy_tofu")], [("grilled_chicken", "roasted_mushrooms")],
                          [("grilled_chicken", None)]])  # leaving it off is always a (last) choice
        self.assertEqual(e.recommended, 0)
        self.assertEqual(e.requirements, e.options[0].requirements)

    def test_premade_ingredient_offers_swap_then_leave_off(self):
        e = self.ev("chicken_bowl", [{"op": "remove", "ingredient": "garlic"}])
        self.assertEqual(e.status, "needs_customer_approval")
        self.assertEqual([changes(o) for o in e.options],
                         [[("garlic_sauce", "sesame_dressing")], [("garlic_sauce", None)]])
        swap, leave_off = e.options
        self.assertEqual(swap.similarity, 0.7)
        self.assertEqual(swap.changes[0]["source"], "chef")
        # Leaving the sauce off has no culinary score; it is not faked as a perfect match.
        self.assertIsNone(leave_off.similarity)
        self.assertEqual(leave_off.changes[0], {"replace": "garlic_sauce", "with": None,
                                                "similarity": None, "source": "leave_off"})

    def test_options_carry_their_full_ops(self):
        e = self.ev("chicken_bowl", [{"op": "remove", "ingredient": "garlic"},
                                     {"op": "add", "ingredient": "avocado"}])
        self.assertEqual(e.base_ops, [{"op": "add", "ingredient": "avocado"}])
        self.assertEqual(e.options[0].ops, [{"op": "add", "ingredient": "avocado"},
                                            {"op": "substitute", "ingredient": "garlic_sauce",
                                             "with": "sesame_dressing"}])
        self.assertEqual(e.options[1].ops[-1], {"op": "remove", "ingredient": "garlic_sauce"})
        for o in e.options:
            self.assertEqual(self.inv.check("chicken_bowl", o.ops).requirements, o.requirements)

    def test_unapproved_swap_offers_approved_ones(self):
        e = self.ev("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": "falafel"}])
        self.assertEqual(e.status, "needs_customer_approval")
        self.assertEqual(e.check.reasons[0].code, "SUBSTITUTE_NOT_APPROVED")  # UI can explain why
        self.assertEqual(len(e.options), 3)  # tofu, mushrooms, or leave the chicken off

    def test_out_of_stock_choice_falls_back_to_other_swap(self):
        self.inv.set_on_hand("tofu", 0, "test")
        e = self.ev("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": "tofu"}])
        self.assertEqual(e.status, "needs_customer_approval")
        self.assertEqual([changes(o) for o in e.options],
                         [[("grilled_chicken", "roasted_mushrooms")], [("grilled_chicken", None)]])

    def test_customer_other_ops_are_kept_in_options(self):
        e = self.ev("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": None},
                                     {"op": "add", "ingredient": "avocado"}])
        self.assertEqual(e.options[0].requirements["avocado"], 120)
        self.assertEqual(e.options[0].price_delta_cents, 200)

    # staff review paths

    def test_allergy_feasible_as_asked_still_needs_review(self):
        e = self.ev("tofu_bowl", level="allergy", allergens=["peanut"])
        self.assertEqual(e.status, "needs_staff_review")
        self.assertTrue(e.requires_staff_review)
        self.assertEqual(e.review_reasons, ["allergy: peanut"])
        self.assertEqual(e.options, [])

    def test_allergy_combines_leave_off_and_swap(self):
        e = self.ev("satay_noodles", level="allergy", allergens=["peanut"])
        self.assertEqual(e.status, "needs_staff_review")
        self.assertEqual([changes(o) for o in e.options], [
            [("crushed_peanuts", None), ("peanut_sauce", "sesame_dressing")],
            [("crushed_peanuts", None), ("peanut_sauce", "garlic_sauce")],
            [("crushed_peanuts", None), ("peanut_sauce", None)],  # customer may leave it off; ranked last
        ])
        for o in e.options:
            self.assertNotIn("peanut", o.check.evidence["allergens_present"])
            self.assertTrue(o.check.requires_staff_review)

    def test_fixture_style_parser_output(self):
        e = self.ev("satay_noodles", [{"op": "remove", "ingredient": "peanuts"},
                                      {"op": "substitute", "ingredient": "peanut_sauce", "with": None}],
                    level="allergy", allergens=["peanut"], severity="severe")
        self.assertEqual(e.status, "needs_staff_review")
        self.assertEqual(changes(e.options[0]), [("peanut_sauce", "sesame_dressing")])
        self.assertNotIn("peanuts", e.requirements)

    def test_allergy_flag_without_allergen_still_reviewed(self):
        e = self.ev("tofu_bowl", level="allergy")
        self.assertEqual(e.status, "needs_staff_review")
        self.assertEqual(e.review_reasons, ["allergy flagged but not specified"])

    def test_diet_ok(self):
        e = self.ev("tofu_bowl", level="diet", diets=["vegan"])
        self.assertEqual(e.status, "needs_staff_review")

    def test_diet_string_accepted(self):
        e = self.ev("tofu_bowl", level="diet", diet="Vegan")
        self.assertEqual(e.review_reasons, ["diet: vegan"])

    def test_diet_unfixable_base_is_unavailable(self):
        e = self.ev("miso_soup", level="diet", diets=["vegan"])
        self.assertEqual(e.status, "unavailable")
        self.assertEqual([r.code for r in e.reasons], ["DIET_CONFLICT"])
        self.assertEqual(e.requirements, {})

    # unavailable / clarification paths

    def test_extra_not_offered_is_unavailable(self):
        e = self.ev("chicken_bowl", [{"op": "add", "ingredient": "tomato"}])
        self.assertEqual(e.status, "unavailable")
        self.assertEqual([r.code for r in e.reasons], ["EXTRA_NOT_OFFERED"])

    def test_unknown_item_needs_clarification(self):
        e = self.ev("chicken_bowl", [{"op": "add", "ingredient": "bacon"}])
        self.assertEqual(e.status, "needs_clarification")

    def test_unknown_dish_needs_clarification(self):
        e = self.ev("pizza")
        self.assertEqual(e.status, "needs_clarification")
        self.assertEqual(e.requirements, {})

    def test_only_leave_off_when_no_swap_is_in_stock(self):
        self.inv.set_on_hand("tofu", 0, "test")
        self.inv.set_on_hand("mushrooms", 0, "test")
        e = self.ev("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": None}])
        self.assertEqual(e.status, "needs_customer_approval")
        self.assertEqual([changes(o) for o in e.options], [[("grilled_chicken", None)]])

    def test_unfixable_reason_blocks_options_even_if_others_fixable(self):
        e = self.ev("chicken_bowl", [{"op": "remove", "ingredient": "garlic"},
                                     {"op": "add", "ingredient": "tomato"}])
        self.assertEqual(e.status, "unavailable")
        self.assertEqual(e.options, [])

    def test_serializes_to_json(self):
        e = self.ev("satay_noodles", level="allergy", allergens=["peanut"])
        data = json.loads(json.dumps(e.to_dict()))
        self.assertEqual(data["status"], "needs_staff_review")
        self.assertEqual(data["options"][0]["changes"][1]["with"], "sesame_dressing")

    def test_accepting_recommendation_holds_and_confirms(self):
        e = self.ev("satay_noodles", level="allergy", allergens=["peanut"])
        self.assertTrue(self.inv.hold("o-1", e.requirements).ok)
        self.assertTrue(self.inv.confirm("o-1").ok)


if __name__ == "__main__":
    unittest.main()
