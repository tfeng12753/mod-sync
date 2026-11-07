"""Check the ranking against independent calculations and directional changes."""
import unittest

from tests.test_decisions import Inventory, decide, raw_request


def option(result):
    return (result.customer_options + result.staff_options + result.unavailable_options)[0]


class ScoreAuditTests(unittest.TestCase):
    def test_formula_bounds_and_threshold_match_diagnostics(self):
        for similarity in [0, 0.60, 0.65, 0.75, 1]:
            with self.subTest(similarity=similarity):
                raw = raw_request()
                raw["evaluation"]["candidates"][0]["similarity"] = similarity
                scored = option(decide(raw))
                f = scored.ranking_factors
                expected = 0.55 * f["similarity"] + 0.35 * f["reserve"] + 0.10 * f["cost"]
                self.assertAlmostEqual(scored.ranking_score, expected)
                self.assertTrue(all(0 <= value <= 1 for value in f.values()))
                self.assertEqual(scored.route == "unavailable", expected < 0.75)

    def test_more_surplus_can_help_but_cannot_bypass_similarity_weight(self):
        low = Inventory()
        low.stock["guacamole"] = 200
        high = Inventory()
        high.stock["guacamole"] = 5000
        a, b = option(decide(inventory=low)), option(decide(inventory=high))
        self.assertLess(a.ranking_score, b.ranking_score)
        self.assertEqual(a.similarity, b.similarity)
        raw = raw_request()
        raw["evaluation"]["candidates"][0]["similarity"] = 0.5
        self.assertEqual(option(decide(raw, inventory=high)).route, "unavailable")

    def test_cost_increase_never_improves_score(self):
        raw = raw_request()
        before = option(decide(raw))
        raw["evaluation"]["candidates"][0]["ingredient_cost_change_cents"] = 200
        after = option(decide(raw))
        self.assertLess(after.ranking_score, before.ranking_score)

