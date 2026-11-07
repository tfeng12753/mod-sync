"""The four demo scenarios from docs/project-idea.md, at the inventory level."""

import unittest

from .helpers import codes, fresh_inventory


class DemoScenarioTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()

    def test_1_close_substitute_available(self):
        r = self.inv.check("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": "tofu"}])
        self.assertEqual(r.status, "ok")

    def test_2_blocked_by_premade_component(self):
        r = self.inv.check("chicken_bowl", [{"op": "remove", "ingredient": "garlic"}])
        self.assertEqual(codes(r), ["PREMADE_INGREDIENT"])

    def test_3_two_feasible_substitutes_handed_to_forecast(self):
        cands = self.inv.substitute_candidates("chicken_bowl", "chicken")
        self.assertEqual(len(cands), 2)
        # Forecast needs these to weigh shortage vs surplus.
        tofu, mush = cands
        self.assertIn("tofu", tofu.available)
        self.assertIn("mushrooms", mush.available)

    def test_4_stock_change_forces_reevaluation_before_confirm(self):
        r = self.inv.check("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": "tofu"}])
        self.assertTrue(r.ok)
        self.inv.set_on_hand("tofu", 100, "staff: dropped a tray")
        confirm = self.inv.confirm("order-9", r.requirements)
        self.assertFalse(confirm.ok)
        self.assertEqual(confirm.code, "INSUFFICIENT_STOCK")
        again = self.inv.check("chicken_bowl", [{"op": "substitute", "ingredient": "chicken", "with": "tofu"}])
        self.assertEqual(again.status, "out_of_stock")


if __name__ == "__main__":
    unittest.main()
