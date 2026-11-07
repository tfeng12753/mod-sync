import asyncio
import copy
import json
import unittest
from io import BytesIO
from unittest.mock import patch
from datetime import datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from backend.engine import (
    EvaluationRequest, OpenRouterExplainer, evaluate_substitutions, forecast_usage,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "evaluation_request.json"


def data():
    payload = json.loads(FIXTURE.read_text())
    payload["forecast"]["model"] = "historical"
    return payload


def evaluate(payload, explainer=None):
    return asyncio.run(evaluate_substitutions(EvaluationRequest.model_validate(payload), explainer))


class EngineTests(unittest.TestCase):
    def test_demo_and_input_immutability(self):
        request = EvaluationRequest.model_validate(data())
        original = request.model_dump_json()
        result = asyncio.run(evaluate_substitutions(request))
        self.assertEqual(result.recommended_option_id, "mushroom_bowl")
        self.assertEqual(result.status, "needs_customer_approval")
        self.assertEqual(request.model_dump_json(), original)
        self.assertLess(result.ranked_options[1].tradeoffs[0].projected_surplus, 0)

    def test_history_changes_recommendation(self):
        payload = data()
        for row in payload["forecast"]["history"]:
            if row["ingredient_id"] == "tofu":
                row["quantity"] = 200
            if row["ingredient_id"] == "mushrooms":
                row["quantity"] = 1400
        self.assertEqual(evaluate(payload).recommended_option_id, "tofu_bowl")

    def test_commitments_are_not_double_counted(self):
        payload = data()
        before = evaluate(payload)
        payload["inventory"]["positions"][0]["committed_in_horizon"] = 500
        after = evaluate(payload)
        a = next(o for o in before.ranked_options if o.option_id == "tofu_bowl").tradeoffs[0]
        b = next(o for o in after.ranked_options if o.option_id == "tofu_bowl").tradeoffs[0]
        self.assertAlmostEqual(a.projected_surplus, b.projected_surplus)
        self.assertEqual(b.available_after_commitments, 1200)
        self.assertAlmostEqual(b.uncommitted_expected_demand, a.uncommitted_expected_demand - 500)

    def test_uncommitted_stock_is_a_hard_constraint(self):
        payload = data()
        payload["inventory"]["positions"][1]["committed_in_horizon"] = 1200
        result = evaluate(payload)
        self.assertEqual(result.recommended_option_id, "tofu_bowl")
        self.assertIn("insufficient", result.excluded_options[0].explanation)

    def test_complete_recipe_checks_shared_ingredient(self):
        payload = data()
        payload["inventory"]["positions"][2]["available_now"] = 100
        result = evaluate(payload)
        self.assertEqual(result.status, "unavailable")
        self.assertIsNone(result.recommended_option_id)

    def test_preparation_and_approval_block_options(self):
        for field in ["approved", "customer_constraints_met", "preparation_feasible"]:
            with self.subTest(field=field):
                payload = data()
                payload["candidates"][0][field] = False
                self.assertEqual(evaluate(payload).recommended_option_id, "tofu_bowl")

    def test_unknown_constraints_require_review(self):
        payload = data()
        for option in payload["candidates"]:
            option["customer_constraints_met"] = None
        result = evaluate(payload)
        self.assertEqual(result.status, "needs_staff_review")
        self.assertIsNone(result.recommended_option_id)

    def test_stale_and_short_horizon_inventory_require_review(self):
        for field, value in [("as_of", "2026-11-07T16:00:00-05:00"),
                             ("valid_until", "2026-11-07T19:00:00-05:00")]:
            with self.subTest(field=field):
                payload = data()
                payload["inventory"][field] = value
                self.assertEqual(evaluate(payload).status, "needs_staff_review")

    def test_missing_history_is_not_zero_demand(self):
        payload = data()
        payload["forecast"]["history"] = []
        result = evaluate(payload)
        self.assertEqual(result.status, "needs_staff_review")
        self.assertIsNone(result.forecasts.forecasts[0].expected_usage)

    def test_stockouts_incomplete_future_and_wrong_source_excluded(self):
        payload = data()
        baseline = evaluate(payload).forecasts
        for index, changes in enumerate([
            {"stockout": True}, {"complete": False}, {"source": "recipe_estimated"}, {},
        ]):
            row = copy.deepcopy(payload["forecast"]["history"][0])
            offset = -index - 1 if index < 3 else 1
            start = datetime.fromisoformat(payload["forecast"]["start"]) + timedelta(days=offset)
            row.update(start=start.isoformat(), end=(start + timedelta(hours=3)).isoformat(), quantity=999999)
            row.update(changes)
            payload["forecast"]["history"].append(row)
        self.assertEqual(evaluate(payload).forecasts, baseline)

    def test_fallback_and_source_selection(self):
        payload = data()
        for row in payload["forecast"]["history"]:
            row["start"] = (datetime.fromisoformat(row["start"]) - timedelta(days=1)).isoformat()
            row["end"] = (datetime.fromisoformat(row["end"]) - timedelta(days=1)).isoformat()
            row["source"] = "recipe_estimated"
        payload["forecast"]["source"] = "recipe_estimated"
        result = evaluate(payload)
        self.assertEqual(result.forecasts.forecasts[0].method, "daypart_fallback")
        self.assertIn("not measured", result.forecasts.forecasts[0].warnings[0])

    def test_no_silent_proration_of_periods(self):
        payload = data()
        payload["forecast"]["end"] = "2026-11-07T19:00:00-05:00"
        self.assertEqual(evaluate(payload).status, "needs_staff_review")

    def test_backtest_uses_only_past_data(self):
        payload = data()
        for row in payload["forecast"]["history"]:
            row["quantity"] = 100
        result = evaluate(payload).forecasts.forecasts[0]
        self.assertEqual(result.expected_usage, 100)
        self.assertEqual(result.backtest_count, 9)
        self.assertEqual(result.backtest_mae, 0)
        self.assertEqual(result.uncertainty_buffer, 10)
        # With only four periods, an outlier in the final period must be unseen
        # by its own forecast and produce a 900-unit one-step error.
        payload["forecast"]["history"] = payload["forecast"]["history"][-12:]
        payload["forecast"]["history"][-3]["quantity"] = 1000
        result = evaluate(payload).forecasts.forecasts[0]
        self.assertEqual(result.backtest_count, 1)
        self.assertEqual(result.backtest_mae, 900)

    def test_very_short_half_life_is_stable(self):
        payload = data()
        payload["forecast"]["half_life_days"] = 0.000001
        result = evaluate(payload)
        self.assertIsNotNone(result.forecasts.forecasts[0].expected_usage)

    def test_schema_rejects_integration_errors(self):
        invalids = []
        payload = data()
        payload["forecast"]["start"] = "2026-11-07T17:00:00"
        invalids.append(payload)
        payload = data()
        payload["forecast"]["history"][0]["unit"] = "ml"
        invalids.append(payload)
        payload = data()
        payload["forecast"]["history"].append(copy.deepcopy(payload["forecast"]["history"][0]))
        invalids.append(payload)
        payload = data()
        payload["candidates"][0]["requirements"].append(copy.deepcopy(payload["candidates"][0]["requirements"][0]))
        invalids.append(payload)
        payload = data()
        payload["candidates"][0]["ingredient_cost_change_cents"] = 1.5
        invalids.append(payload)
        payload = data()
        payload["candidates"][0]["similarity"] = float("nan")
        invalids.append(payload)
        for payload in invalids:
            with self.subTest(payload=payload["forecast"]["start"]):
                with self.assertRaises(ValidationError):
                    EvaluationRequest.model_validate(payload)


class OpenRouterTests(unittest.TestCase):
    def test_actual_http_adapter_request(self):
        async def run():
            client = OpenRouterExplainer("test-key", "test/model", timeout=3)
            with patch("backend.engine.explanations.urlopen") as send:
                send.return_value.__enter__.return_value = BytesIO(b'{"choices": []}')
                reply = await asyncio.to_thread(client._send, {"model": "test/model"})
                request = send.call_args.args[0]
                self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/chat/completions")
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
                self.assertEqual(json.loads(request.data), {"model": "test/model"})
                self.assertEqual(send.call_args.kwargs["timeout"], 3)
                self.assertEqual(reply, {"choices": []})
        asyncio.run(run())

    def test_valid_ai_plan_preserves_decisions(self):
        captured = []

        async def transport(payload):
            captured.append(payload)
            facts = json.loads(payload["messages"][1]["content"])
            return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
                "options": [{"option_id": o["option_id"], "fact_ids": [
                    f["fact_id"] for f in reversed(o["facts"])
                ]} for o in facts["options"]],
            })}}]}

        baseline = evaluate(data())
        explainer = OpenRouterExplainer("test-key", "test/model", transport=transport)
        result = evaluate(data(), explainer)
        self.assertEqual(result.explanation_source, "openrouter")
        self.assertEqual(result.recommended_option_id, baseline.recommended_option_id)
        self.assertEqual([o.score for o in result.ranked_options], [o.score for o in baseline.ranked_options])
        self.assertTrue(result.ranked_options[0].explanation.startswith("Customer price"))
        self.assertTrue(captured[0]["provider"]["require_parameters"])
        self.assertNotIn("test-key", json.dumps(captured))

    def test_invalid_model_output_falls_back(self):
        responses = [
            {"choices": []},
            {"choices": [{"finish_reason": "stop", "message": {"content": "not-json"}}]},
            {"choices": [{"finish_reason": "length", "message": {"content": "{}"}}]},
        ]
        for selections in [[], [{"option_id": "invented", "fact_ids": ["cost"]}],
                           [{"option_id": "mushroom_bowl", "fact_ids": ["cost"]},
                            {"option_id": "tofu_bowl", "fact_ids": ["cost"]}],
                           [{"option_id": "mushroom_bowl", "fact_ids": ["made_up"]},
                            {"option_id": "tofu_bowl", "fact_ids": ["cost"]}]]:
            responses.append({"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"options": selections}),
            }}]})
        baseline = evaluate(data())
        for response in responses:
            async def transport(payload):
                return response
            result = evaluate(data(), OpenRouterExplainer("key", "test/model", transport=transport))
            self.assertEqual(result.explanation_source, "deterministic")
            self.assertEqual(result.ranked_options, baseline.ranked_options)
            self.assertIn("unavailable", result.warnings[-1])

    def test_network_error_and_timeout_fall_back_without_leaking_secrets(self):
        async def error_transport(payload):
            raise OSError("secret-provider-message")

        async def slow_transport(payload):
            await asyncio.sleep(1)

        for transport in [error_transport, slow_transport]:
            result = evaluate(data(), OpenRouterExplainer("key", "test/model", timeout=0.01, transport=transport))
            self.assertEqual(result.explanation_source, "deterministic")
            self.assertNotIn("secret-provider-message", result.model_dump_json())

    def test_no_call_when_no_candidates_are_eligible(self):
        async def transport(payload):
            self.fail("must not call OpenRouter for unavailable recommendations")
        payload = data()
        for option in payload["candidates"]:
            option["approved"] = False
        result = evaluate(payload, OpenRouterExplainer("key", "test/model", transport=transport))
        self.assertEqual(result.status, "unavailable")


if __name__ == "__main__":
    unittest.main()
