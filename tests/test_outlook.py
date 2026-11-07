import asyncio
import copy
import json
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from backend.engine import (
    OpenRouterExplainer, OutlookRequest, assess_extra, build_inventory_outlook,
    forecast_inventory, forecast_usage,
)


def request_data():
    raw = json.loads((Path(__file__).resolve().parents[1] /
                      "fixtures/evaluation_request.json").read_text())
    return {key: raw[key] for key in ["schema_version", "evaluated_at", "forecast", "inventory"]}


class OutlookTests(unittest.TestCase):
    def outlook(self, raw=None):
        payload = raw or request_data()
        payload["forecast"]["model"] = "historical"
        return build_inventory_outlook(OutlookRequest.model_validate(payload))

    def test_upstream_requires_no_candidates(self):
        result = self.outlook()
        self.assertEqual(len(result.ingredients), 3)
        self.assertNotIn("candidates", result.model_dump())

    def test_extra_available_but_consumes_reserve(self):
        extra = assess_extra(self.outlook(), "tofu", 200)
        self.assertTrue(extra.available_now)
        self.assertFalse(extra.preserves_reserve)
        self.assertEqual(extra.decision, "reserve_at_risk")

    def test_extra_supported_and_too_large_extra_unavailable(self):
        outlook = self.outlook()
        supported = assess_extra(outlook, "mushrooms", 200)
        self.assertEqual(supported.decision, "supported")
        self.assertEqual(supported.extra_portions_within_reserve, 4)
        self.assertEqual(assess_extra(outlook, "mushrooms", 1400).decision, "unavailable")

    def test_extra_unknown_and_invalid_quantity(self):
        payload = request_data()
        payload["forecast"]["history"] = []
        result = self.outlook(payload)
        extra = assess_extra(result, "tofu", 200)
        self.assertEqual(extra.decision, "needs_staff_review")
        self.assertIsNone(extra.preserves_reserve)
        for quantity in [0, -1, float("nan"), float("inf")]:
            with self.assertRaises(ValueError):
                assess_extra(result, "tofu", quantity)

    def test_inventory_changes_coverage_not_demand(self):
        payload = request_data()
        original = self.outlook(copy.deepcopy(payload))
        payload["inventory"]["positions"][0]["available_now"] = 10
        changed = self.outlook(payload)
        self.assertEqual(changed.forecasts, original.forecasts)
        self.assertEqual(changed.ingredients[0].risk, "projected_shortage")

    def test_stale_stock_does_not_publish_extra_capacity(self):
        payload = request_data()
        payload["inventory"]["as_of"] = "2026-11-07T16:00:00-05:00"
        result = self.outlook(payload)
        self.assertIsNone(result.ingredients[0].extra_budget_quantity)
        self.assertIsNone(assess_extra(result, "tofu", 200).available_now)

    def test_committed_stock_and_demand_not_double_counted(self):
        payload = request_data()
        before = self.outlook(copy.deepcopy(payload))
        payload["inventory"]["positions"][0]["committed_in_horizon"] = 500
        after = self.outlook(payload)
        self.assertEqual(before.ingredients[0].reserve_headroom, after.ingredients[0].reserve_headroom)

    def test_current_commitment_shortage(self):
        payload = request_data()
        payload["inventory"]["positions"][0]["committed_in_horizon"] = 1800
        self.assertEqual(self.outlook(payload).ingredients[0].risk, "current_shortage")

    def test_real_autoets_and_model_comparison(self):
        request = OutlookRequest.model_validate(request_data())
        result = forecast_usage(request.forecast)
        self.assertTrue(all(f.autoets_backtest_mae is not None for f in result.forecasts))
        for forecast in result.forecasts:
            self.assertGreaterEqual(forecast.expected_usage, 0)
            self.assertEqual(forecast.backtest_count, 3)
            self.assertIn(forecast.model, ["autoets", "historical"])

    def test_autoets_can_learn_trend_beyond_average(self):
        payload = request_data()
        for index, row in enumerate(payload["forecast"]["history"]):
            row["quantity"] = 100 + (index // 3) * 100
        result = forecast_usage(OutlookRequest.model_validate(payload).forecast)
        self.assertTrue(all(f.model == "autoets" for f in result.forecasts))
        self.assertTrue(all(f.expected_usage > 1200 for f in result.forecasts))

    def test_autoets_rejects_missing_week_instead_of_compressing_time(self):
        payload = request_data()
        payload["forecast"]["history"] = payload["forecast"]["history"][:15] + payload["forecast"]["history"][18:]
        result = forecast_usage(OutlookRequest.model_validate(payload).forecast)
        self.assertTrue(all(f.model == "historical" for f in result.forecasts))
        self.assertTrue(all(any("irregular" in w for w in f.warnings) for f in result.forecasts))

    def test_autoets_failure_keeps_baseline(self):
        with patch("backend.engine.models.autoets_prediction", side_effect=ValueError("numerical failure")):
            result = forecast_usage(OutlookRequest.model_validate(request_data()).forecast)
        self.assertTrue(all(f.model == "historical" for f in result.forecasts))
        self.assertTrue(all(any("unavailable" in w for w in f.warnings) for f in result.forecasts))

    def test_upstream_openrouter_selects_only_grounded_facts(self):
        async def transport(payload):
            ingredients = json.loads(payload["messages"][1]["content"])["ingredients"]
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "ingredients": [{"ingredient_id": i["ingredient_id"],
                                 "fact_ids": ["reserve", "forecast"]} for i in ingredients],
            })}}]}
        payload = request_data()
        payload["forecast"]["model"] = "historical"
        result = asyncio.run(forecast_inventory(
            OutlookRequest.model_validate(payload),
            OpenRouterExplainer("key", "test/model", transport=transport),
        ))
        self.assertEqual(result.explanation_source, "openrouter")
        self.assertTrue(result.ingredients[0].explanation.startswith("Inventory coverage"))

    def test_upstream_openrouter_invalid_evidence_falls_back(self):
        async def transport(payload):
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "ingredients": [{"ingredient_id": "invented", "fact_ids": ["forecast", "reserve"]}],
            })}}]}
        payload = request_data()
        payload["forecast"]["model"] = "historical"
        result = asyncio.run(forecast_inventory(
            OutlookRequest.model_validate(payload),
            OpenRouterExplainer("key", "test/model", transport=transport),
        ))
        self.assertEqual(result.explanation_source, "deterministic")
        self.assertIn("unavailable", result.warnings[-1])


if __name__ == "__main__":
    unittest.main()
