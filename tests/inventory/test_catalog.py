import copy
import json
import unittest

from backend.inventory import CatalogError, parse_catalog
from backend.inventory.service import DATA_DIR


def sample():
    with open(DATA_DIR / "catalog.json", encoding="utf-8") as f:
        return json.load(f)


class CatalogTests(unittest.TestCase):
    def test_sample_catalog_loads(self):
        cat = parse_catalog(sample())
        self.assertIn("chicken_bowl", cat.dishes)
        self.assertTrue(cat.components["garlic_sauce"].is_premade)
        self.assertEqual(cat.components["grilled_chicken"].main, "chicken")
        self.assertIn("peanut", cat.ingredients["peanut_butter"].allergens)

    def test_unknown_ingredient_in_component_rejected(self):
        raw = sample()
        raw["components"]["garlic_sauce"]["contains"].append("unicorn")
        with self.assertRaisesRegex(CatalogError, "unknown ingredient 'unicorn'"):
            parse_catalog(raw)

    def test_assembly_main_must_be_used(self):
        raw = sample()
        raw["components"]["rice_base"]["main"] = "tofu"
        with self.assertRaisesRegex(CatalogError, "main 'tofu' must be in uses"):
            parse_catalog(raw)

    def test_substitution_must_replace_component_in_dish(self):
        raw = sample()
        raw["dishes"]["miso_soup"]["substitutions"] = [{"replace": "grilled_chicken", "with": "crispy_tofu"}]
        with self.assertRaisesRegex(CatalogError, "not in the dish"):
            parse_catalog(raw)

    def test_similarity_out_of_range_rejected(self):
        raw = copy.deepcopy(sample())
        raw["dishes"]["chicken_bowl"]["substitutions"][0]["similarity"] = 4  # old 1-5 scale
        with self.assertRaisesRegex(CatalogError, "similarity must be between 0 and 1"):
            parse_catalog(raw)

    def test_negative_similarity_rejected(self):
        raw = copy.deepcopy(sample())
        raw["dishes"]["chicken_bowl"]["substitutions"][0]["similarity"] = -0.1
        with self.assertRaisesRegex(CatalogError, "similarity must be between 0 and 1"):
            parse_catalog(raw)

    def test_non_numeric_similarity_rejected(self):
        raw = copy.deepcopy(sample())
        raw["dishes"]["chicken_bowl"]["substitutions"][0]["similarity"] = "high"
        with self.assertRaisesRegex(CatalogError, "similarity must be a number"):
            parse_catalog(raw)

    def test_sample_similarities_are_on_unit_scale(self):
        cat = parse_catalog(sample())
        scores = [s.similarity for d in cat.dishes.values() for s in d.substitutions if s.similarity is not None]
        self.assertTrue(scores)
        self.assertTrue(all(0 <= s <= 1 for s in scores))

    def test_bad_prep_rejected(self):
        raw = sample()
        raw["components"]["rice_base"]["prep"] = "frozen"
        with self.assertRaises(CatalogError):
            parse_catalog(raw)


if __name__ == "__main__":
    unittest.main()
