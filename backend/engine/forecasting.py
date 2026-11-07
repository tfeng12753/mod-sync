"""Recency-weighted historical baseline with chronological backtesting."""
from datetime import timezone
from math import ceil
from zoneinfo import ZoneInfo

from .schemas import ForecastRequest, ForecastResult, IngredientForecast


def _seconds(start, end):
    return (end.astimezone(timezone.utc) - start.astimezone(timezone.utc)).total_seconds()


def _select(rows, target, config):
    zone = ZoneInfo(config.time_zone)
    target_weekday = target.astimezone(zone).weekday()
    comparable = [r for r in rows if r.start.astimezone(zone).weekday() == target_weekday]
    if len(comparable) >= config.minimum_comparable_periods:
        return comparable, "same_weekday_daypart"
    return rows, "daypart_fallback"


def _mean(rows, target, half_life):
    # Normalize against the latest observation to avoid all weights underflowing
    # when a caller uses a very short half-life. This preserves their ratios.
    latest = max(r.end for r in rows)
    weights = [2 ** (-_seconds(r.end, latest) / 86400 / half_life) for r in rows]
    return sum(r.quantity * w for r, w in zip(rows, weights)) / sum(weights)


def forecast_usage(request: ForecastRequest) -> ForecastResult:
    """Forecast a full, equivalent service period; never silently prorate a day."""
    results = []
    duration = _seconds(request.start, request.end)
    for ingredient in request.ingredients:
        rows = sorted([
            r for r in request.history
            if r.ingredient_id == ingredient.ingredient_id
            and r.source == request.source
            and r.daypart == request.daypart
            and r.complete and not r.stockout
            and r.end <= request.start
            and 0 <= _seconds(r.end, request.start) <= request.lookback_days * 86400
            and abs(_seconds(r.start, r.end) - duration) < 1
        ], key=lambda row: row.start)
        selected, method = _select(rows, request.start, request)
        warnings = []
        if request.source == "recipe_estimated":
            warnings.append("Usage is recipe-estimated, not measured consumption.")
        if len(selected) < request.minimum_comparable_periods:
            results.append(IngredientForecast(
                **ingredient.model_dump(), expected_usage=None, uncertainty_buffer=None,
                method="insufficient_history", source=request.source,
                sample_count=len(selected), backtest_count=0, backtest_mae=None, model="unknown",
                warnings=warnings + ["Too few complete comparable periods; do not assume zero demand."],
            ))
            continue
        if method == "daypart_fallback":
            warnings.append("Sparse weekday history: using other weekdays for this daypart.")
        expected = _mean(selected, request.start, request.half_life_days)
        errors = []
        # Each test period only sees periods completed before that test period began.
        for target in rows:
            past = [r for r in rows if r.end <= target.start and
                    _seconds(r.end, target.start) <= request.lookback_days * 86400]
            training, _ = _select(past, target.start, request)
            if len(training) >= request.minimum_comparable_periods:
                prediction = _mean(training, target.start, request.half_life_days)
                errors.append(abs(target.quantity - prediction))
        chosen_model = "historical"
        baseline_mae = round(sum(errors) / len(errors), 3) if errors else None
        autoets_mae = None
        if request.model == "autoets":
            from .models import evaluate_autoets
            evaluation, message = evaluate_autoets(
                selected, request.start, request, method == "same_weekday_daypart",
            )
            warnings.append(message)
            if evaluation is not None:
                expected = evaluation["expected"]
                chosen_model = evaluation["model"]
                baseline_mae = evaluation["baseline_mae"]
                autoets_mae = evaluation["autoets_mae"]
                errors = evaluation["errors"]
                warnings.append("Model comparison and buffer use selection-window errors; "
                                "they are not an independent accuracy guarantee.")
        if len(errors) >= 3:
            # Empirical absolute-error percentile plus a small operational floor.
            buffer = max(sorted(errors)[ceil(0.9 * len(errors)) - 1], expected * 0.10)
        else:
            buffer = max([expected * 0.25] + [abs(r.quantity - expected) for r in selected])
            warnings.append("Limited backtesting: buffer uses observed variation and a 25% floor.")
        warnings.append("Buffer is a heuristic reserve, not a calibrated prediction interval.")
        results.append(IngredientForecast(
            **ingredient.model_dump(), expected_usage=round(expected, 3),
            uncertainty_buffer=round(buffer, 3), method=method, source=request.source,
            sample_count=len(selected), backtest_count=len(errors),
            backtest_mae=round(sum(errors) / len(errors), 3) if errors else None,
            warnings=warnings,
            model=chosen_model, baseline_backtest_mae=baseline_mae,
            autoets_backtest_mae=autoets_mae,
        ))
    return ForecastResult(start=request.start, end=request.end,
                          daypart=request.daypart, forecasts=results)
