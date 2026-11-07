"""Orchestration adapter for the team's requirements/check/available/confirm API."""
from __future__ import annotations

from datetime import datetime
from pydantic import Field

from .adapters import InventoryGateway
from .schemas import (
    Candidate, Contract, EvaluationRequest, ForecastRequest, IngredientProfile,
    RankingPolicy, Requirement, Restriction,
)
from .service import evaluate_substitutions


class ConnectedEvaluationRequest(Contract):
    evaluated_at: datetime
    forecast: ForecastRequest
    # Restaurant-confirmed boundary: no mid-window replenishment/expiry modeling in v1.
    usable_until: datetime
    candidates: list[Candidate] = Field(min_length=1)
    qty: int = Field(default=1, ge=1)
    restrictions: list[Restriction] = Field(default_factory=list)
    ingredient_profiles: list[IngredientProfile] = Field(default_factory=list)
    staff_review_notes: list[str] = Field(default_factory=list)
    culinary_context: str | None = None
    policy: RankingPolicy = Field(default_factory=RankingPolicy)
    committed_in_horizon: dict[str, float] | None = None
    inventory_version: str | None = None
    max_snapshot_age_seconds: int = Field(default=300, ge=1)


async def evaluate_with_inventory(request: ConnectedEvaluationRequest, gateway: InventoryGateway,
                                  *, explainer=None):
    from math import isclose
    units = {i.ingredient_id: i.unit for i in request.forecast.ingredients}
    candidates = []
    for original in request.candidates:
        if not original.dish_id:
            raise ValueError("connected inventory evaluation requires a dish_id for each candidate")
        candidate = original.model_copy(deep=True)
        base = await gateway.requirements(candidate.dish_id, candidate.changes)
        passed, reasons, checked = await gateway.check(candidate.dish_id, candidate.changes, qty=request.qty)
        expected = {ingredient_id: q * request.qty for ingredient_id, q in base.items()}
        if set(checked) != set(expected) or any(
            not isclose(checked[i], expected[i], rel_tol=1e-9, abs_tol=1e-6) for i in expected
        ):
            passed = None
            reasons.append("Inventory requirements changed or qty semantics disagree; reevaluation required.")
        missing = set(checked) - set(units)
        if missing:
            raise ValueError(f"forecast/unit catalog missing recipe ingredients: {sorted(missing)}")
        candidate.requirements = [Requirement(ingredient_id=i, unit=units[i], quantity=q)
                                  for i, q in checked.items()]
        if not candidate.requirements:
            raise ValueError("inventory returned an empty customized recipe")
        candidate.inventory_check_passed = passed
        candidate.inventory_reasons = reasons
        candidates.append(candidate)
    snapshot = await gateway.snapshot(
        request.forecast.ingredients, as_of=request.evaluated_at, valid_until=request.usable_until,
        committed_in_horizon=request.committed_in_horizon, version=request.inventory_version,
    )
    evaluation = EvaluationRequest(
        evaluated_at=request.evaluated_at, forecast=request.forecast, inventory=snapshot,
        candidates=candidates, restrictions=request.restrictions,
        ingredient_profiles=request.ingredient_profiles, staff_review_notes=request.staff_review_notes,
        culinary_context=request.culinary_context, policy=request.policy,
        max_snapshot_age_seconds=request.max_snapshot_age_seconds,
    )
    # This path is read-only: confirm() is never called during recommendation.
    return await evaluate_substitutions(evaluation, explainer=explainer)
