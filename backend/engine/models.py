"""Existing forecasting models; the inventory level is never a model target."""
from math import isfinite

from .forecasting import _mean


def _regular(rows, target, zone, weekly):
    """Reject gaps rather than compressing stockout/missing dates into adjacent steps."""
    from datetime import timedelta
    starts = [r.start.astimezone(zone).replace(tzinfo=None) for r in rows]
    starts.append(target.astimezone(zone).replace(tzinfo=None))
    interval = timedelta(days=7 if weekly else 1)
    return all(b - a == interval for a, b in zip(starts, starts[1:]))


def autoets_prediction(rows, *, season_length):
    import numpy as np
    from statsforecast.models import AutoETS
    values = np.asarray([r.quantity for r in rows], dtype=np.float64)
    # Restrict errors to additive so true zero-usage periods are supported.
    model = AutoETS(season_length=season_length, model="AZZ" if season_length > 1 else "AZN")
    prediction = float(model.forecast(y=values, h=1)["mean"][0])
    if not isfinite(prediction):
        raise ValueError("AutoETS produced a nonfinite prediction")
    return max(0.0, prediction)


def evaluate_autoets(rows, target, request, weekly):
    """Compare to the baseline on identical rolling origins; return selected model.

    Use the last three periods for model selection; selection errors are explicitly
    labeled and must not be presented as independent accuracy testing.
    Sparse or irregular data uses the known baseline rather than fabricated dates.
    """
    from zoneinfo import ZoneInfo
    minimum = 8
    if len(rows) < minimum + 3:
        return None, "AutoETS needs at least eleven usable regular periods for fitting and validation."
    if not _regular(rows, target, ZoneInfo(request.time_zone), weekly):
        return None, "AutoETS skipped: missing or irregular daily/weekly periods; baseline retained."
    try:
        predictions = []
        for index in range(max(minimum, len(rows) - 3), len(rows)):
            training, heldout = rows[:index], rows[index]
            season = 7 if not weekly and len(training) >= 21 else 1
            model_prediction = autoets_prediction(training, season_length=season)
            baseline_prediction = _mean(training, heldout.start, request.half_life_days)
            predictions.append((abs(heldout.quantity - model_prediction),
                                abs(heldout.quantity - baseline_prediction)))
        validation = predictions[-3:]
        model_mae = sum(p[0] for p in validation) / len(validation)
        baseline_mae = sum(p[1] for p in validation) / len(validation)
        choose_model = model_mae < baseline_mae
        final = autoets_prediction(rows, season_length=7 if not weekly and len(rows) >= 21 else 1)
        errors = [p[0 if choose_model else 1] for p in validation]
        return {
            "expected": final if choose_model else _mean(rows, target, request.half_life_days),
            "model": "autoets" if choose_model else "historical",
            "autoets_mae": round(model_mae, 3), "baseline_mae": round(baseline_mae, 3),
            "errors": errors,
        }, ("AutoETS selected using three rolling validation periods." if choose_model else
            "Historical baseline retained: AutoETS did not beat it on three rolling validation periods.")
    except (ImportError, ValueError, RuntimeError, ArithmeticError) as exc:
        return None, f"AutoETS unavailable ({type(exc).__name__}); baseline retained."
