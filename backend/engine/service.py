from .explanations import OpenRouterExplainer
from .forecasting import forecast_usage
from .ranking import rank_candidates
from .schemas import EvaluationRequest, EvaluationResult
from .outlook import OutlookRequest, InventoryOutlook, build_inventory_outlook


async def forecast_inventory(
    request: OutlookRequest, explainer: OpenRouterExplainer | None = None,
) -> InventoryOutlook:
    """Primary upstream API: no recipe candidates or culinary similarity required."""
    import asyncio
    # Model fitting is CPU-bound; keep it off an async API server's event loop.
    result = await asyncio.to_thread(build_inventory_outlook, request)
    return await explainer.explain_outlook(result) if explainer else result


async def evaluate_substitutions(
    request: EvaluationRequest, explainer: OpenRouterExplainer | None = None,
) -> EvaluationResult:
    """API integration entry point; input inventory is never mutated."""
    import asyncio
    forecasts = await asyncio.to_thread(forecast_usage, request.forecast)
    result = rank_candidates(request, forecasts)
    return await explainer.explain(result) if explainer else result
