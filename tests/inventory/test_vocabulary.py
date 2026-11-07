import unittest

from backend.inventory import CatalogError
from backend.inventory.export import FIXTURES, exports, render
from backend.inventory.vocabulary import Vocabulary

from .helpers import fresh_inventory


class LookupTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()

    def test_nickname(self):
        self.assertEqual(self.inv.lookup("avo", "chicken_bowl"), ["avocado"])

    def test_normalizes_case_punctuation_plural_articles(self):
        self.assertEqual(self.inv.lookup("The Avocados!"), ["avocado"])

    def test_the_sauce_means_this_dishes_sauce(self):
        self.assertEqual(self.inv.lookup("the sauce", "chicken_bowl"), ["garlic_sauce"])
        self.assertEqual(self.inv.lookup("the sauce", "satay_noodles"), ["peanut_sauce"])
        self.assertEqual(self.inv.lookup("dressing", "tofu_bowl"), ["sesame_dressing"])

    def test_ambiguous_without_dish(self):
        self.assertEqual(len(self.inv.lookup("sauce")), 4)

    def test_component_beats_ingredient_inside_it(self):
        self.assertEqual(self.inv.lookup("tahini", "falafel_wrap"), ["tahini_sauce"])
        self.assertEqual(self.inv.lookup("onions", "chicken_bowl"), ["pickled_onion"])

    def test_ingredient_in_premade_still_found(self):
        self.assertEqual(self.inv.lookup("garlic", "chicken_bowl"), ["garlic"])

    def test_swap_targets_in_scope(self):
        self.assertEqual(self.inv.lookup("tofu", "chicken_bowl"), ["tofu"])
        self.assertEqual(self.inv.lookup("greens", "chicken_bowl"), ["mixed_greens"])

    def test_unknown(self):
        self.assertEqual(self.inv.lookup("cilantro", "chicken_bowl"), [])

    def test_dish_allergen_diet_aliases(self):
        self.assertEqual(self.inv.vocab.lookup_dish("peanut noodles"), ["satay_noodles"])
        self.assertEqual(self.inv.vocab.allergen("Gluten"), "wheat")
        self.assertEqual(self.inv.vocab.allergen("sesame"), "sesame")
        self.assertEqual(self.inv.vocab.diet("plant based"), "vegan")

    def test_bad_alias_id_rejected(self):
        with self.assertRaises(CatalogError):
            Vocabulary(self.inv.catalog, {"items": {"unicorn": ["uni"]}})


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.inv = fresh_inventory()

    def test_vocabulary_shape(self):
        v = self.inv.parser_vocabulary()
        self.assertEqual(set(v["ops"]), {"remove", "add", "substitute", "on_side", "less"})
        self.assertIn("peanut", v["allergens"])
        self.assertEqual(v["diets"], ["vegan", "vegetarian"])
        chicken = next(d for d in v["dishes"] if d["id"] == "chicken_bowl")
        garlic = next(i for i in chicken["items"] if i["id"] == "garlic")
        self.assertEqual(garlic["in"], ["garlic_sauce"])
        sauce = next(i for i in chicken["items"] if i["id"] == "garlic_sauce")
        self.assertIn("note", sauce)  # premade warning

    def test_menu(self):
        menu = {d["id"]: d for d in self.inv.menu()}
        self.assertEqual(menu["chicken_bowl"]["price_cents"], 1450)
        self.assertIn("peanut", menu["satay_noodles"]["allergens"])
        self.assertEqual(menu["miso_soup"]["swaps"], [])

    def test_fixture_files_are_up_to_date(self):
        for name, data in exports().items():
            with self.subTest(file=name):
                on_disk = (FIXTURES / name).read_text(encoding="utf-8")
                self.assertEqual(on_disk, render(data),
                                 f"fixtures/{name} is stale: run `py -3 -m backend.inventory.export`")


if __name__ == "__main__":
    unittest.main()
