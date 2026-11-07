"""Verify the six demo orders using live Sonnet 5.5 and a fresh local API per case."""

from __future__ import annotations

import argparse
from getpass import getpass
import os
import re
import sys
import unittest

from fastapi.testclient import TestClient
import httpx

from backend.api.app import create_app
from backend.api.service import OrderService
from backend.parsing.adapter import parse_request


MODEL = "anthropic/claude-sonnet-5.5"


class SonnetClient:
    def __init__(self, http, key):
        self.http = http
        self.key = key
        self.models = []

    def post(self, url, headers, json):
        response = self.http.post(url, headers={**headers, "Authorization": f"Bearer {self.key}"},
                                  json={**json, "model": MODEL})
        response.raise_for_status()
        model = response.json().get("model", "")
        if not re.fullmatch(re.escape(MODEL) + r"(?:-\d{8}|-\d{4}-\d{2}-\d{2})?", model):
            raise ValueError("OpenRouter did not report a Sonnet 5.5 model for this response")
        self.models.append(model)
        return response


class DemoCases(unittest.TestCase):
    def setUp(self):
        def live_parse(*args):
            return parse_request(*args, mode="openrouter", client=self.provider)

        self.api = self.enterContext(TestClient(create_app(OrderService(parse_fn=live_parse))))

    def post(self, path, **body):
        response = self.api.post(f"/api/{path}", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def evaluate(self, dish_id, text="As listed, please."):
        # Use an explicit no-change note to exercise Sonnet even for plain orders. The normal
        # blank-note path intentionally confirms from inventory without paying for a model call.
        before = len(self.provider.models)
        result = self.post("evaluate", dish_id=dish_id, text=text, channel="doordash")
        self.assertEqual(len(self.provider.models), before + 1)
        self.assertEqual(result["parsed"]["source"], "openrouter")
        self.assertFalse(result["warnings"], result)
        return result

    def accept(self, result, option_id=None):
        return self.post(f"orders/{result['order_id']}/accept", option_id=option_id)

    def test_1_crispy_tofu_bowl(self):
        """Crispy Tofu Bowl goes through at menu price."""
        result = self.evaluate("tofu_bowl")
        self.assertEqual(result["status"], "ready", result)
        self.assertEqual(result["parsed"]["ops"], [])
        self.assertEqual(result["as_requested"]["price_change_cents"], 0)
        self.assertEqual(self.accept(result)["status"], "confirmed")

    def test_2_garlic_chicken_with_extras(self):
        """Garlic Chicken Bowl offers a sauce change and charges $5.50 for the extras."""
        result = self.evaluate("chicken_bowl", "No garlic, extra chicken, and double avocado")
        self.assertEqual(result["status"], "needs_customer_approval", result)
        options = [o for o in result["options"] if any(c["replace"] == "garlic_sauce" for c in o["changes"])]
        self.assertTrue(options, result)
        for option in options:
            self.assertEqual(option["price_change_cents"], 550)
            self.assertIn("EXTRA Grilled chicken x1", option["kitchen_instructions"])
            self.assertIn("EXTRA Sliced avocado x1", option["kitchen_instructions"])
        self.assertEqual(self.accept(result, options[0]["option_id"])["status"], "confirmed")

    def test_3_vegetarian_chicken_bowl(self):
        """Vegetarian Garlic Chicken Bowl offers mushrooms and requires staff approval."""
        result = self.evaluate("chicken_bowl", "I’m vegetarian")
        self.assertEqual(result["status"], "needs_customer_approval", result)
        self.assertIn({"kind": "diet", "value": "vegetarian"}, result["parsed"]["restrictions"])
        self.assertTrue(result["review"]["required"])
        option = next((o for o in result["options"] if any(
            c["replace"] == "grilled_chicken" and c["with"] == "roasted_mushrooms" for c in o["changes"])), None)
        self.assertIsNotNone(option, result)
        self.assertEqual(option["route"], "staff")
        self.assertEqual(option["price_change_cents"], 0)
        option_id = option["option_id"]
        self.assertEqual(self.accept(result, option_id)["status"], "awaiting_staff")
        review = self.post(f"orders/{result['order_id']}/review", action="approve")
        self.assertEqual(review["status"], "awaiting_customer")
        self.assertEqual(self.accept(result, option_id)["status"], "confirmed")

    def test_4_miso_soup_without_miso(self):
        """Miso cannot be removed from Miso Soup."""
        result = self.evaluate("miso_soup", "No miso")
        self.assertEqual(result["status"], "unavailable", result)
        self.assertIsNone(result["order_id"])
        self.assertIn("miso", result["customer_message"].lower())

    def test_5_falafel_wrap_without_sand(self):
        """Falafel Wrap with 'no sand' goes through without a recipe change."""
        result = self.evaluate("falafel_wrap", "No sand")
        self.assertEqual(result["status"], "ready", result)
        self.assertEqual(result["parsed"]["ops"], [])
        self.assertEqual(result["as_requested"]["kitchen_instructions"], [])
        self.assertEqual(self.accept(result)["status"], "confirmed")

    def test_6_satay_noodles_with_peanut_sauce_out(self):
        """Satay Noodles names the unavailable peanut sauce and offers an in-stock swap."""
        self.post("stock/peanut_sauce", on_hand=0, reason="demo test only")
        result = self.evaluate("satay_noodles")
        self.assertEqual(result["status"], "needs_customer_approval", result)
        self.assertTrue(any("Peanut sauce" in reason and "only 0 available" in reason
                            for reason in result["as_requested"]["reasons"]), result)
        self.assertTrue(result["options"], result)
        for option in result["options"]:
            self.assertTrue(any(c["replace"] == "peanut_sauce" for c in option["changes"]), option)
        order = self.accept(result, result["recommended_option_id"])
        self.assertEqual(order["status"], "confirmed")


def run_suite(provider, case_filter=None):
    loader = unittest.TestLoader()
    if case_filter:
        loader.testNamePatterns = [f"*{case_filter}*"]
    suite = loader.loadTestsFromTestCase(DemoCases)
    for case in suite:
        case.provider = provider
    return unittest.TextTestRunner(verbosity=2).run(suite)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("-k", metavar="CASE", help="Run only cases whose test name contains CASE")
    args = cli.parse_args()
    key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
    if not key:
        if not sys.stdin.isatty():
            sys.exit("Run in an interactive terminal for a private key prompt, or set OPENROUTER_API_KEY there.")
        key = getpass("OpenRouter API key (hidden): ").strip()
    if not key:
        sys.exit("No OpenRouter API key provided.")
    print(f"Running live requests against {MODEL}. Each uses isolated demo stock.", flush=True)
    with httpx.Client(timeout=30.0) as http:
        provider = SonnetClient(http, key)
        result = run_suite(provider, args.k)
    print(f"Verified Sonnet 5.5 responses: {len(provider.models)}/{result.testsRun}", flush=True)
    return 0 if result.testsRun and result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
