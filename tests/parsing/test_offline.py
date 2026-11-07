"""Offline (rule-based) mode on the shared parser cases and a few extra phrasings."""

import unittest

from backend.inventory import load_inventory
from backend.parsing.adapter import parse_request

from .helpers import load_cases

# Cases offline mode handles differently on purpose or can't handle (also listed in the README):
#  - conflict: offline keeps only the "actually" correction (-> ready, like the LLM prompt asks).
#  - make-it-a-bowl: "a bowl instead of a wrap" names the dish, not the tortilla; offline sends
#    unknown:wrap -> needs_clarification (fails closed).
NOT_SUPPORTED = {"conflict", "make-it-a-bowl"}


def offline(inv, dish, text, qty=1, clarifications=()):
    return parse_request(inv, dish, qty, text, clarifications=clarifications, mode="offline")


class OfflineParserCases(unittest.TestCase):
    def test_supported_cases_reach_inventory_status(self):
        for case in load_cases():
            if case["id"] in NOT_SUPPORTED:
                continue
            with self.subTest(case=case["id"]):
                inv = load_inventory()
                parsed = offline(inv, case["dish"], case["text"], case["qty"])
                self.assertEqual(parsed.source, "offline")
                ev = inv.evaluate_request(parsed.parser_output)
                self.assertEqual(ev.status, case["inventory_status"], parsed.parser_output)
                flags = case["expected"]["flags"] or {}
                self.assertEqual(sorted(parsed.parser_output["flags"]["allergens"]), sorted(flags.get("allergens", [])))
                self.assertEqual(sorted(parsed.parser_output["flags"]["diets"]), sorted(flags.get("diets", [])))

    def test_easy_cases_give_exact_ops(self):
        exact = {"remove-simple", "extra-nickname", "double-means-one-extra", "sauce-on-side", "light-sauce",
                 "remove-onions-alias", "premade-ingredient", "swap-specified", "swap-unspecified",
                 "swap-unapproved", "unknown-item", "unknown-remove", "not-representable", "extra-not-offered",
                 "extra-over-max", "multiple-ops", "extra-from-other-dish", "diet-only", "allergy-not-in-dish",
                 "allergy-casual", "allergy-explicit", "gluten-alias", "side-premade-sauce",
                 "component-beats-ingredient", "allergy-sesame", "diet-unmeetable", "qty-from-ui"}
        for case in load_cases():
            if case["id"] not in exact:
                continue
            with self.subTest(case=case["id"]):
                parsed = offline(load_inventory(), case["dish"], case["text"], case["qty"])
                key = lambda o: (o["op"], o["ingredient"])
                self.assertEqual(sorted(parsed.parser_output["ops"], key=key),
                                 sorted(case["expected"]["ops"], key=key))


class OfflineExtras(unittest.TestCase):
    def setUp(self):
        self.inv = load_inventory()

    def test_correction_keeps_final_intent(self):
        parsed = offline(self.inv, "chicken_bowl", "no avocado. actually extra avocado")
        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.parser_output["ops"], [{"op": "add", "ingredient": "avocado", "qty": 1}])

    def test_contradiction_asks(self):
        parsed = offline(self.inv, "chicken_bowl", "no avocado and extra avocado")
        self.assertEqual(parsed.status, "needs_clarification")
        self.assertTrue(parsed.questions)
        self.assertEqual(parsed.parser_output["ops"], [])

    def test_gibberish_asks(self):
        parsed = offline(self.inv, "chicken_bowl", "thanks so much!")
        self.assertEqual(parsed.status, "needs_clarification")
        self.assertTrue(parsed.questions)

    def test_swap_phrasings(self):
        parsed = offline(self.inv, "chicken_bowl", "replace the garlic mayo with sesame dressing")
        self.assertEqual(parsed.parser_output["ops"], [{"op": "substitute", "ingredient": "garlic_sauce", "with": "sesame_dressing"}])
        self.assertEqual(self.inv.evaluate_request(parsed.parser_output).status, "ready")

    def test_unknown_replacement(self):
        parsed = offline(self.inv, "chicken_bowl", "bacon instead of chicken")
        self.assertIn({"op": "substitute", "ingredient": "chicken", "with": None}, parsed.parser_output["ops"])
        self.assertIn({"op": "add", "ingredient": "unknown:bacon", "qty": 1}, parsed.parser_output["ops"])
        self.assertEqual(self.inv.evaluate_request(parsed.parser_output).status, "needs_clarification")

    def test_restrictions_without_item_ops(self):
        parsed = offline(self.inv, "chicken_bowl", "I'm vegetarian, no meat")
        self.assertEqual(parsed.parser_output["ops"], [])
        self.assertEqual(parsed.restrictions, [{"kind": "diet", "value": "vegetarian"}])
        parsed = offline(self.inv, "satay_noodles", "nut free please")
        self.assertEqual(parsed.status, "needs_staff_review")
        self.assertIn({"kind": "allergy", "value": "peanut"}, parsed.restrictions)

    def test_restriction_in_clarification_answer(self):
        parsed = offline(self.inv, "chicken_bowl", "no avocado",
                         clarifications=[{"question": "Anything else?", "answer": "sesame allergy"}])
        self.assertEqual(parsed.status, "needs_staff_review")
        self.assertEqual(parsed.parser_output["flags"]["allergens"], ["sesame"])

    def test_tomato_lookup_workaround(self):
        parsed = offline(self.inv, "falafel_wrap", "no tomatoes")
        self.assertEqual(parsed.parser_output["ops"], [{"op": "remove", "ingredient": "tomato"}])


if __name__ == "__main__":
    unittest.main()
