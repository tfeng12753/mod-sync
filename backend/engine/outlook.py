"""Upstream demand and inventory outlook, independent of substitutions."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from .forecasting import forecast_usage
from .schemas import Contract, DecisionFact, ForecastRequest, ForecastResult, Ingredient, InventorySnapshot, Restriction
from .constraints import SAFETY_REVIEW_NOTE


class OutlookRequest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    evaluated_at: datetime
    forecast: ForecastRequest
    inventory: InventorySnapshot
    max_snapshot_age_seconds: int = Field(default=300, ge=1)
    restrictions: list[Restriction] = Field(default_factory=list)
    staff_review_notes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def same_window(self):
        if self.evaluated_at != self.forecast.start:
            raise ValueError("v1 evaluates at the start of the forecast window")
        return self


class IngredientOutlook(Ingredient):
    risk: Literal["covered", "reserve_at_risk", "projected_shortage", "current_shortage", "unknown"]
    available_after_commitments: float | None
    remaining_demand: float | None
    projected_balance: float | None
    reserve_headroom: float | None
    # Capacity for additional portions while preserving the forecast reserve.
    extra_budget_quantity: float | None
    reasons: list[str]
    facts: list[DecisionFact] = Field(default_factory=list)
    explanation: str = ""


class InventoryOutlook(Contract):
    schema_version: Literal["1.0"] = "1.0"
    evaluated_at: datetime
    inventory_version: str
    forecasts: ForecastResult
    ingredients: list[IngredientOutlook]
    warnings: list[str]
    explanation_source: Literal["deterministic", "openrouter"] = "deterministic"
    staff_review_notes: list[str] = Field(default_factory=list)


class ExtraAssessment(Ingredient):
    quantity: float
    available_now: bool | None
    preserves_reserve: bool | None
    extra_portions_within_reserve: int | None
    decision: Literal["supported", "reserve_at_risk", "unavailable", "needs_staff_review"]
    reason: str
    staff_review_notes: list[str] = Field(default_factory=list)


def build_inventory_outlook(request: OutlookRequest) -> InventoryOutlook:
    forecasts = forecast_usage(request.forecast)
    stock = {p.ingredient_id: p for p in request.inventory.positions}
    age = (request.evaluated_at - request.inventory.as_of).total_seconds()
    snapshot_valid = (0 <= age <= request.max_snapshot_age_seconds and
                      request.inventory.valid_until >= request.forecast.end)
    results = []
    for forecast in forecasts.forecasts:
        position = stock.get(forecast.ingredient_id)
        reasons = []
        available = remaining = balance = headroom = budget = None
        if not snapshot_valid:
            reasons.append("Inventory is stale, future-dated, or does not cover the forecast horizon.")
        elif position is None or position.unit != forecast.unit:
            reasons.append("Inventory is missing or uses a different unit.")
        else:
            available = position.available_now - position.committed_in_horizon
        if forecast.expected_usage is None:
            reasons.append("Demand forecast unavailable; never treat unknown demand as zero.")
        risk = "unknown"
        if not reasons:
            remaining = max(0.0, forecast.expected_usage - position.committed_in_horizon)
            balance = available - remaining
            headroom = balance - forecast.uncertainty_buffer
            budget = max(0.0, headroom)
            risk = ("current_shortage" if available < 0 else "projected_shortage" if balance < 0
                    else "reserve_at_risk" if headroom < 0 else "covered")
        facts = [DecisionFact(fact_id="forecast", text=(
            f"{forecast.ingredient_id}: expected usage is {forecast.expected_usage} {forecast.unit}; "
            f"reserve buffer is {forecast.uncertainty_buffer} {forecast.unit}."
            if forecast.expected_usage is not None else
            f"{forecast.ingredient_id}: demand forecast is unknown."
        )), DecisionFact(fact_id="reserve", text=(
            f"Inventory coverage is {risk}; reserve headroom is {headroom:.1f} {forecast.unit}."
            if headroom is not None else "Inventory coverage is unknown. " + " ".join(reasons)
        ))]
        if budget is not None:
            facts.append(DecisionFact(fact_id="extras", text=(
                f"Up to {budget:.1f} {forecast.unit} is forecast reserve capacity for extras, "
                "shared across orders; the base order must already be accounted for."
            )))
        results.append(IngredientOutlook(
            ingredient_id=forecast.ingredient_id, unit=forecast.unit, risk=risk,
            available_after_commitments=round(available, 3) if available is not None else None,
            remaining_demand=round(remaining, 3) if remaining is not None else None,
            projected_balance=round(balance, 3) if balance is not None else None,
            reserve_headroom=round(headroom, 3) if headroom is not None else None,
            extra_budget_quantity=round(budget, 3) if budget is not None else None,
            reasons=reasons, facts=facts, explanation=" ".join(f.text for f in facts),
        ))
    return InventoryOutlook(
        evaluated_at=request.evaluated_at, inventory_version=request.inventory.version,
        forecasts=forecasts, ingredients=results,
        staff_review_notes=list(dict.fromkeys(request.staff_review_notes +
            [r.staff_review_note for r in request.restrictions] +
            ([SAFETY_REVIEW_NOTE] if request.restrictions else []))),
        warnings=["Extra budgets are advisory and shared across requests, not reservations. "
                  "Recheck the inventory version and atomically reserve on confirmation. "
                  "The base order must already be accounted for before assessing an extra."] +
                 (["Only net availability is known; reserving gross forecast demand is conservative "
                   "and may overstate shortages."] if not request.inventory.commitments_known else []) +
                 (["Inventory has no authoritative snapshot version; confirmation must call check again."]
                  if not request.inventory.version_is_authoritative else []),
    )


def assess_extra(outlook: InventoryOutlook, ingredient_id: str, quantity: float) -> ExtraAssessment:
    """Assess one *additional* portion after the base order is accounted for."""
    from math import floor, isfinite
    if not isfinite(quantity) or quantity <= 0:
        raise ValueError("extra quantity must be positive and finite")
    ingredient = next((i for i in outlook.ingredients if i.ingredient_id == ingredient_id), None)
    if ingredient is None:
        raise ValueError("ingredient is absent from the outlook")
    available = (ingredient.available_after_commitments >= quantity
                 if ingredient.available_after_commitments is not None else None)
    preserves = (ingredient.extra_budget_quantity >= quantity
                 if ingredient.extra_budget_quantity is not None else None)
    decision = ("needs_staff_review" if ingredient.risk == "unknown" else
                "unavailable" if not available else
                "supported" if preserves else "reserve_at_risk")
    if outlook.staff_review_notes and decision != "unavailable":
        decision = "needs_staff_review"
    reason = {
        "needs_staff_review": "Staff review is required; stock and forecasts do not certify safety or dietary compliance.",
        "unavailable": "There is not enough uncommitted stock for this extra portion.",
        "supported": "This extra portion fits within stock and the forecast reserve.",
        "reserve_at_risk": "The portion is available now but would use forecast reserve capacity.",
    }[decision]
    return ExtraAssessment(
        ingredient_id=ingredient_id, unit=ingredient.unit, quantity=quantity,
        available_now=available, preserves_reserve=preserves,
        extra_portions_within_reserve=(floor(ingredient.extra_budget_quantity / quantity)
                                      if ingredient.extra_budget_quantity is not None else None),
        decision=decision, reason=reason,
        staff_review_notes=outlook.staff_review_notes,
    )
