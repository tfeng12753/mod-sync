"""parse_request(): catalog -> parser request, fake model output -> evaluate_request() input.

Every test is offline: the model is a FakeClient, never a real OpenRouter call.
"""

import os
import unittest
from unittest import mock

from backend.inventory import load_inventory
from backend.parsing.adapter import (
    ParsedRequest,
    build_parse_request,
    dish_recipe,
    parse_request,
    to_parser_output,
)
from backend.parsing.parser import (
    SYSTEM_PROMPT,
    CartItem,
    ParseRequest,
    ParseResult,
    ParserValidationError,
)

from .helpers import FakeClient, change, load_cases, model_output, model_output_for_case

# Parser design keeps only the correction ("no avocado. actually extra avocado" -> add), and
# rejects duplicate (item, ingredient) changes, so this case is tested separately below.
CONFLICT_CASE = "conflict"


def run(inv, dish, text, output, qty=1, clarifications=()):
    client = FakeClient(output)
    parsed = parse_request(inv, dish, qty, text, clarifications=clarifications, client=client, mode="openrouter")
    return parsed, client


def sort_ops(ops):
    return sorted(ops, key=lambda o: (o["op"], o["ingredient"]))


class FixtureCasesWithFakeModel(unittest.TestCase):
    def test_every_case_reaches_expected_inventory_status(self):
        for case in load_cases():
            if case["id"] == CONFLICT_CASE:
                continue
            with self.subTest(case=case["id"]):
                inv = load_inventory()
                parsed, _ = run(inv, case["dish"], case["text"], model_output_for_case(case), qty=case["qty"])
                self.assertIsInstance(parsed, ParsedRequest)
                self.assertEqual(parsed.source, "openrouter")
                out = parsed.parser_output
                self.assertEqual(out["dish"], case["dish"])
                self.assertEqual(out["qty"], case["qty"])
                self.assertEqual(out["original_text"], case["text"])
                self.assertEqual(sort_ops(out["ops"]), sort_ops(case["expected"]["ops"]))
                flags = case["expected"]["flags"] or {}
                self.assertEqual(sorted(out["flags"]["allergens"]), sorted(flags.get("allergens", [])))
                self.assertEqual(sorted(out["flags"]["diets"]), sorted(flags.get("diets", [])))
                self.assertEqual(out["flags"]["level"], flags.get("level"))
                ev = inv.evaluate_request(out)
                self.assertEqual(ev.status, case["inventory_status"], [r.code for r in ev.check.reasons])
                if "first_option" in case:
                    got = [[c["replace"], c["with"]] for c in ev.options[ev.recommended].changes]
                    self.assertEqual(got, case["first_option"])
                if flags:
                    self.assertEqual(parsed.status, "needs_staff_review")

    def test_conflict_case(self):
        case = next(c for c in load_cases() if c["id"] == CONFLICT_CASE)
        dish = case["dish"]
        inv = load_inventory()
        # Passing both through would be a duplicate (item, ingredient): rejected as unsafe.
        with self.assertRaises(ParserValidationError):
            run(inv, dish, case["text"], model_output(changes=[
                change(dish, "avocado", "remove"), change(dish, "avocado", "add", 1)]))
        # The explicit correction is the final intent.
        parsed, _ = run(inv, dish, case["text"], model_output(changes=[change(dish, "avocado", "add", 1)]))
        self.assertEqual(parsed.parser_output["ops"], [{"op": "add", "ingredient": "avocado", "qty": 1}])
        self.assertEqual(inv.evaluate_request(parsed.parser_output).status, "ready")
        # A model unsure about it asks instead.
        parsed, _ = run(inv, dish, case["text"], model_output("needs_clarification", questions=["No avocado or extra?"]))
        self.assertEqual(parsed.status, "needs_clarification")
        self.assertEqual(parsed.parser_output["ops"], [])
        self.assertEqual(parsed.questions, ["No avocado or extra?"])


class RequestBuilding(unittest.TestCase):
    def setUp(self):
        self.inv = load_inventory()

    def test_recipe_has_components_and_their_ingredients(self):
        recipe = dish_recipe(self.inv, "chicken_bowl")
        for item in ("garlic_sauce", "sliced_avocado", "grilled_chicken", "rice_base", "pickled_onion",
                     "garlic", "avocado", "chicken", "rice", "red_onion"):
            self.assertEqual(recipe[item], 1, item)
        self.assertNotIn("tofu", recipe)
        # IDs that are both a component and an ingredient appear once.
        self.assertEqual(dish_recipe(self.inv, "falafel_wrap")["tomato"], 1)

    def test_allowed_ids_cover_catalog_and_payload_has_dish_vocabulary(self):
        req = build_parse_request(self.inv, "falafel_wrap", 2, "no tahini")
        cat = self.inv.catalog
        self.assertEqual(set(req.allowed_ingredient_ids), {*cat.ingredients, *cat.components})
        self.assertEqual([c.cart_item_id for c in req.cart], ["falafel_wrap"])
        self.assertEqual(req.vocabulary["dish"]["id"], "falafel_wrap")
        self.assertEqual(req.vocabulary["quantity_ordered"], 2)
        self.assertIn("wheat", req.vocabulary["allergens"])
        self.assertEqual(req.vocabulary["allergen_aliases"]["gluten"], "wheat")

    def test_prompt_payload_sent_to_model(self):
        parsed, client = run(self.inv, "chicken_bowl", "extra avo", model_output(changes=[change("chicken_bowl", "avocado", "add", 1)]))
        body = client.calls[-1]["json"]
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertEqual(body["messages"][0]["content"], SYSTEM_PROMPT)
        payload = client.payload
        self.assertEqual(payload["original_customer_instructions"], "extra avo")
        names = {i["id"]: i for i in payload["menu_vocabulary"]["dish"]["items"]}
        self.assertIn("avo", names["avocado"]["aliases"])
        self.assertNotIn("burger", SYSTEM_PROMPT.lower())

    def test_clarifications_are_forwarded(self):
        parsed, client = run(self.inv, "chicken_bowl", "no sauce", model_output(changes=[change("chicken_bowl", "garlic_sauce", "remove")]),
                             clarifications=[{"question": "Which sauce?", "answer": "the garlic one"}])
        self.assertEqual(client.payload["clarification_history"], [{"question": "Which sauce?", "answer": "the garlic one"}])

    def test_unknown_dish_and_bad_qty(self):
        with self.assertRaises(KeyError):
            parse_request(self.inv, "pizza", 1, "no cheese", mode="offline")
        with self.assertRaises(ValueError):
            parse_request(self.inv, "chicken_bowl", 0, "no avocado", mode="offline")

    def test_empty_text_is_ready_without_model_call(self):
        client = FakeClient("not json")
        parsed = parse_request(self.inv, "chicken_bowl", 1, "  ", client=client)
        self.assertEqual(parsed.status, "ready")
        self.assertEqual(parsed.parser_output["ops"], [])
        self.assertEqual(client.calls, [])


class Translation(unittest.TestCase):
    def setUp(self):
        self.inv = load_inventory()

    def test_set_compares_with_recipe(self):
        dish = "chicken_bowl"
        parsed, _ = run(self.inv, dish, "make it 3 avocado, 0 onion and 1 sauce", model_output(changes=[
            change(dish, "avocado", "set", 3),
            change(dish, "pickled_onion", "set", 0),
            change(dish, "garlic_sauce", "set", 1),
        ]))
        self.assertEqual(parsed.parser_output["ops"], [
            {"op": "add", "ingredient": "avocado", "qty": 2},
            {"op": "remove", "ingredient": "pickled_onion"},
        ])

    def test_set_below_recipe_is_less_and_absent_item_is_add(self):
        req = ParseRequest(instructions="x", cart=[CartItem(cart_item_id="d", name="D", ingredients={"avocado": 2})],
                           allowed_ingredient_ids=["avocado", "tofu"])
        result = ParseResult(status="ready", questions=[], staff_notes=[], changes=[
            {"cart_item_id": "d", "ingredient_id": "avocado", "operation": "set", "quantity": 1},
            {"cart_item_id": "d", "ingredient_id": "tofu", "operation": "set", "quantity": 2},
        ])
        out = to_parser_output(self.inv, req, result, "d", 1)
        self.assertEqual(out["ops"], [{"op": "less", "ingredient": "avocado"}, {"op": "add", "ingredient": "tofu", "qty": 2}])

    def test_ingredient_named_like_a_part_means_the_part(self):
        # "no tahini": the model may pick the tahini ingredient (made ahead inside the sauce).
        dish = "falafel_wrap"
        parsed, _ = run(self.inv, dish, "no tahini", model_output(changes=[change(dish, "tahini", "remove")]))
        self.assertEqual(parsed.parser_output["ops"], [{"op": "remove", "ingredient": "tahini_sauce"}])
        self.assertEqual(self.inv.evaluate_request(parsed.parser_output).status, "ready")
        # An ingredient that is not a part's alias is left alone ("no garlic" stays garlic).
        parsed, _ = run(self.inv, "chicken_bowl", "no garlic", model_output(changes=[change("chicken_bowl", "garlic", "remove")]))
        self.assertEqual(parsed.parser_output["ops"], [{"op": "remove", "ingredient": "garlic"}])

    def test_model_cannot_add_something_the_customer_never_named(self):
        dish = "chicken_bowl"
        for text, wrong in [("add sand", change(dish, "avocado", "add", 1)),
                            ("swap the chicken for gravel", change(dish, "chicken", "substitute", 0, "crispy_tofu"))]:
            with self.subTest(text=text):
                parsed, _ = run(self.inv, dish, text, model_output(changes=[wrong]))
                self.assertEqual(parsed.status, "needs_clarification")
                self.assertEqual(parsed.parser_output["ops"], [])
                self.assertIn("Sorry", parsed.questions[0])

    def test_named_additions_pass_the_guard(self):
        for dish, text, ch in [
            ("tofu_bowl", "double tofu", change("tofu_bowl", "crispy_tofu", "add", 1)),
            ("chicken_bowl", "extra avo!!", change("chicken_bowl", "avocado", "add", 1)),
            ("chicken_bowl", "more chicken please", change("chicken_bowl", "grilled_chicken", "add", 1)),
            ("chicken_bowl", "mushrooms instead of chicken", change("chicken_bowl", "chicken", "substitute", 0, "roasted_mushrooms")),
            ("chicken_bowl", "no chicken, something else", change("chicken_bowl", "chicken", "substitute", 0, None)),
        ]:
            with self.subTest(text=text):
                parsed, _ = run(self.inv, dish, text, model_output(changes=[ch]))
                self.assertEqual(parsed.status, "ready")
                self.assertEqual(len(parsed.parser_output["ops"]), 1)

    def test_stray_quantity_on_substitute_is_ignored(self):
        dish = "falafel_wrap"
        parsed, _ = run(self.inv, dish, "garlic sauce instead of tahini",
                        model_output(changes=[change(dish, "tahini_sauce", "substitute", 1, "garlic_sauce")]))
        self.assertEqual(parsed.parser_output["ops"], [{"op": "substitute", "ingredient": "tahini_sauce", "with": "garlic_sauce"}])

    def test_substitute_with_null(self):
        dish = "chicken_bowl"
        parsed, _ = run(self.inv, dish, "no chicken, something else", model_output(changes=[change(dish, "chicken", "substitute", 0, None)]))
        self.assertEqual(parsed.parser_output["ops"], [{"op": "substitute", "ingredient": "chicken", "with": None}])
        self.assertEqual(self.inv.evaluate_request(parsed.parser_output).status, "needs_customer_approval")

    def test_on_side_less_remove(self):
        dish = "chicken_bowl"
        parsed, _ = run(self.inv, dish, "sauce on side, light onion, no rice", model_output(changes=[
            change(dish, "garlic_sauce", "on_side"), change(dish, "pickled_onion", "less"), change(dish, "rice", "remove")]))
        self.assertEqual(parsed.parser_output["ops"], [
            {"op": "on_side", "ingredient": "garlic_sauce"}, {"op": "less", "ingredient": "pickled_onion"},
            {"op": "remove", "ingredient": "rice"}])

    def test_unknown_items_become_unknown_ops(self):
        dish = "chicken_bowl"
        parsed, _ = run(self.inv, dish, "add bacon, swap the cilantro", model_output(
            "needs_staff_review", staff_notes=["bacon and cilantro aren't on our list"], unknown_items=[
                {"cart_item_id": dish, "word": "Bacon", "operation": "add"},
                {"cart_item_id": dish, "word": "fresh cilantro", "operation": "substitute"}]))
        self.assertEqual(parsed.parser_output["ops"], [
            {"op": "add", "ingredient": "unknown:bacon", "qty": 1},
            {"op": "substitute", "ingredient": "unknown:fresh_cilantro", "with": None}])
        self.assertEqual(self.inv.evaluate_request(parsed.parser_output).status, "needs_clarification")

    def test_ready_with_unknown_item_is_forced_to_staff_review(self):
        dish = "chicken_bowl"
        parsed, _ = run(self.inv, dish, "add bacon", model_output(unknown_items=[{"cart_item_id": dish, "word": "bacon", "operation": "add"}]))
        self.assertEqual(parsed.status, "needs_staff_review")
        self.assertTrue(parsed.staff_notes)


class KeywordBackstop(unittest.TestCase):
    """The fake model 'forgets' the restriction; the deterministic backstop must add it."""

    def setUp(self):
        self.inv = load_inventory()

    def check(self, dish, text, kind, value, level):
        parsed, _ = run(self.inv, dish, text, model_output())
        self.assertEqual(parsed.status, "needs_staff_review", text)
        self.assertIn({"kind": kind, "value": value}, parsed.restrictions, text)
        self.assertEqual(parsed.parser_output["flags"]["level"], level, text)
        self.assertTrue(parsed.staff_notes)
        return parsed

    def test_allergies(self):
        p = self.check("satay_noodles", "my kid is allergic to peanuts", "allergy", "peanut", "allergy")
        self.assertEqual(self.inv.evaluate_request(p.parser_output).status, "needs_staff_review")
        p = self.check("satay_noodles", "I'm celiac, can this be gluten free?", "allergy", "wheat", "allergy")
        self.assertEqual(self.inv.evaluate_request(p.parser_output).status, "unavailable")
        self.check("falafel_wrap", "sesame allergy", "allergy", "sesame", "allergy")
        self.check("chicken_bowl", "gluten-free please", "allergy", "wheat", "allergy")

    def test_unknown_allergen_is_kept(self):
        p = self.check("chicken_bowl", "I have a dairy allergy", "allergy", "dairy", "allergy")
        self.assertEqual(self.inv.evaluate_request(p.parser_output).status, "needs_staff_review")

    def test_diets(self):
        p = self.check("tofu_bowl", "I'm vegan", "diet", "vegan", "diet")
        self.assertEqual(self.inv.evaluate_request(p.parser_output).status, "needs_staff_review")
        self.check("tofu_bowl", "plant-based please", "diet", "vegan", "diet")
        self.check("chicken_bowl", "I'm vegetarian", "diet", "vegetarian", "diet")

    def test_allergy_beats_diet(self):
        p = self.check("tofu_bowl", "vegan, and allergic to soy", "allergy", "soy", "allergy")
        self.assertIn({"kind": "diet", "value": "vegan"}, p.restrictions)

    def test_allergy_mention_without_allergen_still_flags(self):
        parsed, _ = run(self.inv, "chicken_bowl", "I have allergies, no avocado",
                        model_output(changes=[change("chicken_bowl", "avocado", "remove")]))
        self.assertEqual(parsed.status, "needs_staff_review")
        self.assertEqual(parsed.restrictions, [])
        self.assertEqual(parsed.parser_output["flags"]["level"], "allergy")
        self.assertEqual(self.inv.evaluate_request(parsed.parser_output).status, "needs_staff_review")

    def test_religious_diet_goes_to_staff_without_untracked_diet_value(self):
        parsed, _ = run(self.inv, "chicken_bowl", "is the chicken halal?",
                        model_output("needs_staff_review", staff_notes=["halal"], restrictions=[{"kind": "diet", "value": "halal"}]))
        self.assertEqual(parsed.status, "needs_staff_review")
        self.assertEqual(parsed.restrictions, [])
        self.assertEqual(parsed.parser_output["flags"], {"level": "diet", "allergens": [], "diets": []})
        self.assertTrue(any("halal" in n for n in parsed.staff_notes))
        self.assertEqual(self.inv.evaluate_request(parsed.parser_output).status, "needs_staff_review")

    def test_model_values_are_normalized(self):
        parsed, _ = run(self.inv, "satay_noodles", "no gluten for me",
                        model_output("needs_staff_review", staff_notes=["gluten"],
                                     restrictions=[{"kind": "allergy", "value": "Gluten"}, {"kind": "allergy", "value": "peanuts"}]))
        self.assertEqual(parsed.parser_output["flags"]["allergens"], ["wheat", "peanut"])

    def test_answer_in_clarification_is_checked(self):
        parsed, _ = run(self.inv, "chicken_bowl", "no sauce", model_output(changes=[change("chicken_bowl", "garlic_sauce", "remove")]),
                        clarifications=[{"question": "Any reason?", "answer": "I'm allergic to eggs"}])
        self.assertEqual(parsed.status, "needs_staff_review")
        self.assertIn({"kind": "allergy", "value": "egg"}, parsed.restrictions)

    def test_clarification_with_allergy_becomes_staff_review_keeping_questions(self):
        parsed, _ = run(self.inv, "chicken_bowl", "peanut allergy, and change the thing",
                        model_output("needs_clarification", questions=["What should we change?"]))
        self.assertEqual(parsed.status, "needs_staff_review")
        self.assertEqual(parsed.questions, ["What should we change?"])


class UnsafeModelOutput(unittest.TestCase):
    def setUp(self):
        self.inv = load_inventory()
        self.dish = "chicken_bowl"

    def rejects(self, output, text="no avocado"):
        with self.assertRaises(ParserValidationError):
            run(self.inv, self.dish, text, output)

    def test_invented_ids(self):
        d = self.dish
        self.rejects(model_output(changes=[change(d, "bacon", "add", 1)]))
        self.rejects(model_output(changes=[change(d, "chicken", "substitute", 0, "lobster")]))
        self.rejects(model_output(changes=[change("burger-1", "avocado", "remove")]))
        self.rejects(model_output(unknown_items=[{"cart_item_id": "burger-1", "word": "bacon", "operation": "add"}]))

    def test_bad_operations(self):
        d = self.dish
        self.rejects(model_output(changes=[change(d, "tomato", "on_side")]))      # not in recipe
        self.rejects(model_output(changes=[change(d, "tofu", "less")]))           # not in recipe
        self.rejects(model_output(changes=[change(d, "avocado", "add", 0)]))
        self.rejects(model_output(changes=[change(d, "avocado", "remove", 1)]))
        self.rejects(model_output(changes=[change(d, "avocado", "set", -1)]))
        self.rejects(model_output(changes=[change(d, "avocado", "remove", 0, "tofu")]))  # with on non-substitute
        self.rejects(model_output(changes=[change(d, "chicken", "substitute", 0, "chicken")]))
        self.rejects(model_output(changes=[change(d, "avocado", "remove"), change(d, "avocado", "add", 1)]))
        self.rejects(model_output(changes=[change(d, "avocado", "explode")]))

    def test_inconsistent_status(self):
        d = self.dish
        self.rejects(model_output("ready", changes=[change(d, "avocado", "remove")], questions=["?"]))
        self.rejects(model_output("needs_clarification", changes=[change(d, "avocado", "remove")], questions=["?"]))
        self.rejects(model_output("needs_clarification"))
        self.rejects(model_output("needs_staff_review"))

    def test_malformed_json(self):
        self.rejects("not json")
        self.rejects('{"status": "ready"}')
        self.rejects("")


class Modes(unittest.TestCase):
    def setUp(self):
        self.inv = load_inventory()

    def test_default_mode_without_key_is_offline(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            parsed = parse_request(self.inv, "chicken_bowl", 1, "no avocado")
        self.assertEqual(parsed.source, "offline")
        self.assertEqual(parsed.parser_output["ops"], [{"op": "remove", "ingredient": "avocado"}])

    def test_default_mode_with_client_or_key_is_openrouter(self):
        out = model_output(changes=[change("chicken_bowl", "avocado", "remove")])
        parsed = parse_request(self.inv, "chicken_bowl", 1, "no avocado", client=FakeClient(out))
        self.assertEqual(parsed.source, "openrouter")
        client = FakeClient(out)
        with mock.patch.dict(os.environ, {"OPENROUTER_API_KEY": "fake-test-value"}):
            with mock.patch("backend.parsing.parser.httpx.Client") as http:
                http.return_value.__enter__.return_value = client
                parsed = parse_request(self.inv, "chicken_bowl", 1, "no avocado")
        self.assertEqual(parsed.source, "openrouter")
        self.assertEqual(len(client.calls), 1)

    def test_openrouter_without_key_or_client_fails(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENROUTER_API_KEY", None)
            with self.assertRaises(RuntimeError):
                parse_request(self.inv, "chicken_bowl", 1, "no avocado", mode="openrouter")

    def test_bad_mode(self):
        with self.assertRaises(ValueError):
            parse_request(self.inv, "chicken_bowl", 1, "no avocado", mode="magic")


if __name__ == "__main__":
    unittest.main()
