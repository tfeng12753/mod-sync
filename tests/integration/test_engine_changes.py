"""Tests for the one engine change made for the inventory integration:
``ModificationOffer.removed_requirements_verified`` (see docs/integration-plan.md, "Engine changes").
Uses the engine's own synthetic decision fixture from tests/test_decisions.py."""

import unittest

from pydantic import ValidationError

from backend.engine.decisions import ModificationOffer
from tests.test_decisions import decide, raw_request


def swap_with_shared_ingredient(verified: bool):
    """Original recipe rice 200 + guacamole 60; replacement removes the guacamole AND 100 g of
    rice (inventory says that rice belonged to the replaced component) and adds 60 g beans."""
    raw = raw_request()
    raw["action"] = "substitute"
    raw["original_changes"]["recipe"].update(guacamole=60)
    raw["evaluation"]["candidates"][0]["changes"]["recipe"] = {"rice": 100, "beans": 60}
    raw["offers"][0].update(offered_item_id="beans", portion_requirements={"beans": 60},
                            removed_requirements={"guacamole": 60, "rice": 100},
                            removed_requirements_verified=verified)
    return raw


class VerifiedRemovalTests(unittest.TestCase):
    def test_default_is_unverified_and_unchanged(self):
        self.assertFalse(ModificationOffer(option_id="x", approved_for_dish=True).removed_requirements_verified)
        result = decide(swap_with_shared_ingredient(verified=False))
        self.assertEqual(result.status, "unavailable")
        self.assertTrue(any("unrelated" in r for r in result.unavailable_options[0].reasons))

    def test_inventory_attested_removal_of_shared_ingredient_is_accepted(self):
        result = decide(swap_with_shared_ingredient(verified=True))
        self.assertEqual(result.status, "reply_to_customer")
        option = result.customer_options[0]
        self.assertEqual(option.removed_requirements, {"guacamole": 60, "rice": 100})
        self.assertEqual(option.requirements, {"rice": 100, "beans": 60})

    def test_verified_removal_must_still_remove_the_target(self):
        raw = swap_with_shared_ingredient(verified=True)
        raw["original_changes"]["recipe"] = {"rice": 200}
        raw["evaluation"]["candidates"][0]["changes"]["recipe"] = {"rice": 100, "beans": 60}
        raw["offers"][0]["removed_requirements"] = {"rice": 100}
        result = decide(raw)
        self.assertEqual(result.status, "unavailable")
        self.assertTrue(any("does not remove the targeted" in r for r in result.unavailable_options[0].reasons))

    def test_verified_removal_cannot_exceed_original(self):
        raw = swap_with_shared_ingredient(verified=True)
        raw["offers"][0]["removed_requirements"] = {"guacamole": 60, "rice": 300}
        self.assertEqual(decide(raw).status, "unavailable")

    def test_verified_removal_still_requires_exact_final_recipe(self):
        raw = swap_with_shared_ingredient(verified=True)
        raw["offers"][0]["removed_requirements"] = {"guacamole": 60, "rice": 50}
        result = decide(raw)
        self.assertEqual(result.status, "unavailable")
        self.assertTrue(any("does not match" in r for r in result.unavailable_options[0].reasons))

    def test_verified_flag_requires_explicit_removals(self):
        with self.assertRaises(ValidationError):
            ModificationOffer(option_id="x", approved_for_dish=True, removed_requirements_verified=True)


if __name__ == "__main__":
    unittest.main()
