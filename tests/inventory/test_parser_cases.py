"""The shared parser test set (fixtures/parser_cases.json) must be valid and line up with inventory.

These run the *expected* parser outputs through evaluate_request(), so they prove the parser ->
inventory contract works before the parser exists. Person 2 can reuse the same file to score the
real parser.
"""

import json
import unittest
from pathlib import Path

from backend.inventory.models import OPS
from backend.inventory.vocabulary import UNKNOWN_PREFIX

from .helpers import fresh_inventory

CASES_FILE = Path(__file__).resolve().parents[2] / "fixtures" / "parser_cases.json"


def load_cases():
    with open(CASES_FILE, encoding="utf-8") as f:
        return json.load(f)["cases"]


class ParserCaseTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()
        self.vocab = self.inv.parser_vocabulary()

    def test_case_ids_unique(self):
        ids = [c["id"] for c in load_cases()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_expected_outputs_only_use_vocabulary(self):
        cat = self.inv.catalog
        known_allergens = set(self.vocab["allergens"])
        known_diets = set(self.vocab["diets"])
        for case in load_cases():
            with self.subTest(case=case["id"]):
                self.assertIn(case["dish"], cat.dishes)
                for op in case["expected"]["ops"]:
                    self.assertIn(op["op"], OPS)
                    for key in ("ingredient", "with"):
                        val = op.get(key)
                        if val is None or val.startswith(UNKNOWN_PREFIX):
                            continue
                        self.assertTrue(val in cat.ingredients or val in cat.components, f"{val!r} not in catalog")
                flags = case["expected"]["flags"] or {}
                self.assertTrue(set(flags.get("allergens", [])) <= known_allergens)
                self.assertTrue(set(flags.get("diets", [])) <= known_diets)

    def test_expected_outputs_give_expected_inventory_status(self):
        for case in load_cases():
            with self.subTest(case=case["id"]):
                inv = fresh_inventory()
                parser_output = {"dish": case["dish"], "qty": case["qty"], "original_text": case["text"],
                                 **case["expected"]}
                ev = inv.evaluate_request(parser_output)
                self.assertEqual(ev.status, case["inventory_status"],
                                 f"{case['text']!r}: {[r.code for r in ev.check.reasons]}")
                if "first_option" in case:
                    self.assertIsNotNone(ev.recommended)
                    got = [[c["replace"], c["with"]] for c in ev.options[ev.recommended].changes]
                    self.assertEqual(got, case["first_option"])


if __name__ == "__main__":
    unittest.main()
