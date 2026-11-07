"""Call-count, context isolation, concurrency, and safety regressions; no real provider requests."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import os
from threading import Event
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.api.app import create_app
from backend.api.service import OrderService
from backend.inventory import load_inventory
from backend.parsing.adapter import build_parse_request, parse_request, to_parser_output
from backend.parsing.cache import ParseCache, parse_with_cache
from backend.parsing.menu_phrases import parse_menu_phrase
from backend.parsing.parser import ParserValidationError
from tests.api.test_api import fake_route
from .helpers import FakeClient, change, load_cases, model_output


class MenuPhraseTests(unittest.TestCase):
    def setUp(self):
        self.inv = load_inventory()

    def test_every_recognized_fixture_preserves_expected_ops_and_inventory_outcome(self):
        count = 0
        for case in load_cases():
            request = build_parse_request(self.inv, case["dish"], case["qty"], case["text"])
            result = parse_menu_phrase(self.inv, request, case["dish"])
            if result is None:
                continue
            count += 1
            with self.subTest(case=case["id"]):
                output = to_parser_output(self.inv, request, result, case["dish"], case["qty"])
                sort_ops = lambda ops: sorted(ops, key=lambda op: (op["op"], op["ingredient"]))
                self.assertEqual(sort_ops(output["ops"]), sort_ops(case["expected"]["ops"]))
                self.assertEqual(self.inv.evaluate_request(output).status, case["inventory_status"])
        self.assertGreaterEqual(count, 10)

    def test_safety_dialogue_partial_matches_and_ambiguous_text_never_take_shortcut(self):
        texts = [
            "no avocado, peanut allergy", "no chicken, I'm vegetarian", "no garlic for my allergy",
            "no avocado unless it is ripe", "no avocado and extra avocado", "swap chicken for caviar",
            "no avocado except on one bowl", "no avocado but extra chicken", "no avocado; add caviar",
            "extra avocado and don't remove the chicken", "add avocado instead of removing it",
            "no avocado, kosher please", "no peanut traces", "extra avocado not too much",
        ]
        for text in texts:
            with self.subTest(text=text):
                request = build_parse_request(self.inv, "chicken_bowl", 1, text)
                self.assertIsNone(parse_menu_phrase(self.inv, request, "chicken_bowl"))
        request = build_parse_request(self.inv, "chicken_bowl", 1, "extra avocado",
                                      [{"question": "Any restrictions?", "answer": "peanut allergy"}])
        self.assertIsNone(parse_menu_phrase(self.inv, request, "chicken_bowl"))

    def test_auto_mode_uses_no_provider_for_common_aliases_and_quantity(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with patch("backend.parsing.parser.httpx.Client") as http:
                result = parse_request(self.inv, "chicken_bowl", 2,
                                       "Could you please hold the onions, add 2 avo, and put sauce on the side please?")
        http.assert_not_called()
        self.assertEqual(result.source, "menu")
        self.assertEqual(result.parser_output["ops"], [
            {"op": "remove", "ingredient": "pickled_onion"},
            {"op": "add", "ingredient": "avocado", "qty": 2},
            {"op": "on_side", "ingredient": "garlic_sauce"},
        ])

    def test_forced_model_mode_and_disabled_shortcut_still_call_provider(self):
        for extra_env, mode in [({}, "openrouter"), ({"PARSER_FAST_PATH_ENABLED": "0"}, None)]:
            with self.subTest(mode=mode):
                fake = FakeClient(model_output(changes=[change("chicken_bowl", "avocado", "remove")]))
                with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key", **extra_env}):
                    with patch("backend.parsing.cache._PARSE_CACHE", ParseCache()):
                        with patch("backend.parsing.parser.httpx.Client") as http:
                            http.return_value.__enter__.return_value = fake
                            parsed = parse_request(self.inv, "chicken_bowl", 1, "no avocado", mode=mode)
                self.assertEqual(parsed.source, "openrouter")
                self.assertEqual(len(fake.calls), 1)

    def test_changed_alias_and_recipe_are_used_immediately(self):
        request = build_parse_request(self.inv, "chicken_bowl", 1, "no avo")
        self.assertIsNotNone(parse_menu_phrase(self.inv, request, "chicken_bowl"))
        self.inv.vocab._items.pop("avo")
        self.assertIsNone(parse_menu_phrase(self.inv, request, "chicken_bowl"))
        dish = self.inv.catalog.dishes["chicken_bowl"]
        components = {key: amount for key, amount in dish.components.items() if key != "garlic_sauce"}
        self.inv.catalog.dishes[dish.id] = replace(dish, components=components)
        request = build_parse_request(self.inv, dish.id, 1, "garlic sauce on the side")
        self.assertIsNone(parse_menu_phrase(self.inv, request, dish.id))


class ModelCacheTests(unittest.TestCase):
    def setUp(self):
        self.inv = load_inventory()
        self.request = build_parse_request(self.inv, "chicken_bowl", 1, "please leave the avocado out of my bowl")
        self.output = model_output(changes=[change("chicken_bowl", "avocado", "remove")])
        self.cache = ParseCache()

    def parse(self, request=None, client=None):
        return parse_with_cache(request or self.request, client=client or self.client, cache=self.cache)

    def test_identical_request_calls_model_once_and_returns_independent_copies(self):
        self.client = FakeClient(self.output)
        first, first_hit = self.parse()
        first.changes.clear()
        second, second_hit = self.parse()
        self.assertFalse(first_hit)
        self.assertTrue(second_hit)
        self.assertEqual(len(self.client.calls), 1)
        self.assertEqual(len(second.changes), 1)

    def test_text_dish_quantity_recipe_alias_history_model_prompt_and_key_are_isolated(self):
        self.client = FakeClient(model_output())
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "key-one", "OPENROUTER_MODEL": "model-one"}):
            self.parse()
            variants = [
                self.request.model_copy(update={"instructions": "please don't leave out avocado"}, deep=True),
                build_parse_request(self.inv, "tofu_bowl", 1, self.request.instructions),
                build_parse_request(self.inv, "chicken_bowl", 2, self.request.instructions),
                build_parse_request(self.inv, "chicken_bowl", 1, self.request.instructions,
                                    [{"question": "Why?", "answer": "peanut allergy"}]),
            ]
            recipe = self.request.model_copy(deep=True)
            recipe.cart[0].ingredients["avocado"] = 2
            variants.append(recipe)
            vocabulary = self.request.model_copy(deep=True)
            vocabulary.vocabulary["dish"]["items"][0]["aliases"].append("new alias")
            variants.append(vocabulary)
            for request in variants:
                self.assertFalse(self.parse(request)[1])
            with patch.dict(os.environ, {"OPENROUTER_MODEL": "model-two"}):
                self.assertFalse(self.parse()[1])
            with patch.dict(os.environ, {"OPENROUTER_API_KEY": "key-two"}):
                self.assertFalse(self.parse()[1])
            with patch("backend.parsing.parser.SYSTEM_PROMPT", "updated interpreter rules"):
                self.assertFalse(self.parse()[1])
        self.assertEqual(len(self.client.calls), 10)

    def test_safety_backstop_runs_on_cached_result(self):
        self.client = FakeClient(self.output)
        request = build_parse_request(self.inv, "chicken_bowl", 1, "no avocado, peanut allergy")
        for expected_hit in (False, True):
            result, hit = self.parse(request)
            self.assertEqual(hit, expected_hit)
            self.assertEqual(result.status, "needs_staff_review")
            self.assertIn("allergy", result.safety_flags)
            self.assertEqual(result.restrictions[0].value, "peanut")
        self.assertEqual(len(self.client.calls), 1)

    def test_disabling_cache_calls_provider_for_each_request(self):
        self.client = FakeClient(self.output)
        with patch.dict(os.environ, {"PARSER_CACHE_ENABLED": "0"}):
            for _ in range(2):
                self.assertFalse(self.parse()[1])
        self.assertEqual(len(self.client.calls), 2)

    def test_invalid_provider_output_and_transport_failures_are_not_cached(self):
        self.client = FakeClient("invalid json")
        for _ in range(2):
            with self.assertRaises(ParserValidationError):
                self.parse()
        self.assertEqual(len(self.client.calls), 2)
        self.client = FakeClient(self.output)
        result, hit = self.parse()
        self.assertFalse(hit)
        self.assertEqual(len(result.changes), 1)

    def test_expiry_capacity_and_failed_computations(self):
        now = [0]
        cache = ParseCache(max_entries=2, ttl_seconds=10, clock=lambda: now[0])
        self.assertFalse(cache.get_or_compute("a", lambda: {"value": 1})[1])
        self.assertTrue(cache.get_or_compute("a", lambda: {})[1])
        cache.get_or_compute("b", lambda: {})
        cache.get_or_compute("c", lambda: {})
        self.assertFalse(cache.get_or_compute("a", lambda: {"value": 2})[1])
        now[0] = 10
        self.assertFalse(cache.get_or_compute("a", lambda: {"value": 3})[1])
        def fail():
            raise RuntimeError("provider unavailable")
        with self.assertRaises(RuntimeError):
            cache.get_or_compute("failure", fail)
        self.assertFalse(cache.get_or_compute("failure", lambda: {})[1])

    def test_concurrent_identical_requests_share_one_call(self):
        cache, started, release = ParseCache(), Event(), Event()
        calls = []
        def compute():
            calls.append(1)
            started.set()
            if not release.wait(5):
                raise TimeoutError("test release timed out")
            return {"value": [1]}
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(cache.get_or_compute, "same", compute)
            self.assertTrue(started.wait(2))
            others = [pool.submit(cache.get_or_compute, "same", compute) for _ in range(3)]
            # A different request can complete while the first is waiting on its provider.
            self.assertEqual(cache.get_or_compute("other", lambda: {"value": 2})[0], {"value": 2})
            release.set()
            results = [first.result(), *(future.result() for future in others)]
        self.assertEqual(len(calls), 1)
        self.assertEqual(sum(not hit for _, hit in results), 1)


class CachedApiTests(unittest.TestCase):
    def test_cached_interpretation_rechecks_stock_prices_and_full_safety_context(self):
        inv = load_inventory()
        client = FakeClient(model_output(changes=[change("chicken_bowl", "avocado", "add", 1)]))
        service = OrderService(inv=inv, route_fn=fake_route)
        api = TestClient(create_app(service))
        payload = {"dish_id": "chicken_bowl", "text": "I would like an additional serving of avocado"}
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            with patch("backend.parsing.cache._PARSE_CACHE", ParseCache()):
                with patch("backend.parsing.parser.httpx.Client") as http:
                    http.return_value.__enter__.return_value = client
                    first = api.post("/api/evaluate", json=payload).json()
                    self.assertEqual(first["parsed"]["source"], "openrouter")
                    self.assertEqual(first["as_requested"]["price_change_cents"], 200)
                    api.post(f"/api/orders/{first['order_id']}/cancel")
                    dish = inv.catalog.dishes["chicken_bowl"]
                    extras = dict(dish.extras)
                    extras["sliced_avocado"] = replace(extras["sliced_avocado"], price_cents=250)
                    inv.catalog.dishes[dish.id] = replace(dish, extras=extras)
                    second = api.post("/api/evaluate", json=payload).json()
                    self.assertEqual(second["parsed"]["source"], "openrouter_cache")
                    self.assertEqual(second["as_requested"]["price_change_cents"], 250)
                    api.post(f"/api/orders/{second['order_id']}/cancel")
                    inv.set_on_hand("avocado", 0, "test stock change")
                    third = api.post("/api/evaluate", json=payload).json()
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(third["status"], "unavailable")
        self.assertIsNone(third["order_id"])
