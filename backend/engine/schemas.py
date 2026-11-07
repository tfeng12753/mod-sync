"""Version-one contracts. All time windows are half-open: [start, end)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Unit = Literal["g", "ml", "each"]
Source = Literal["measured", "recipe_estimated"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    @field_validator("*", mode="after")
    @classmethod
    def aware_timestamps(cls, value):
        if isinstance(value, datetime) and value.utcoffset() is None:
            raise ValueError("timestamps must include a UTC offset")
        return value


class Ingredient(Contract):
    ingredient_id: str = Field(min_length=1)
    unit: Unit


class UsageRecord(Ingredient):
    start: datetime
    end: datetime
    daypart: str = Field(min_length=1)
    quantity: float = Field(ge=0)
    source: Source
    stockout: bool = False
    complete: bool = True

    @model_validator(mode="after")
    def valid_window(self):
        if self.end <= self.start:
            raise ValueError("usage end must follow start")
        return self


class ForecastRequest(Contract):
    start: datetime
    end: datetime
    time_zone: str
    daypart: str = Field(min_length=1)
    ingredients: list[Ingredient] = Field(min_length=1)
    history: list[UsageRecord]
    source: Source = "measured"
    lookback_days: int = Field(default=90, ge=1, le=730)
    half_life_days: float = Field(default=28, gt=0)
    minimum_comparable_periods: int = Field(default=3, ge=2)
    model: Literal["autoets", "historical"] = "autoets"

    @model_validator(mode="after")
    def valid_request(self):
        if self.end <= self.start:
            raise ValueError("forecast end must follow start")
        try:
            ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("time_zone must be an IANA timezone") from exc
        units = {i.ingredient_id: i.unit for i in self.ingredients}
        if len(units) != len(self.ingredients):
            raise ValueError("ingredient IDs must be unique")
        seen = set()
        for row in self.history:
            if row.ingredient_id in units and row.unit != units[row.ingredient_id]:
                raise ValueError("history units must match ingredient units")
            key = (row.ingredient_id, row.start, row.end, row.source)
            if key in seen:
                raise ValueError("duplicate usage period")
            seen.add(key)
        # One total per ingredient/period/source: overlapping totals double-count usage.
        groups = {}
        for row in self.history:
            groups.setdefault((row.ingredient_id, row.source), []).append(row)
        for rows in groups.values():
            ordered = sorted(rows, key=lambda row: row.start)
            if any(b.start < a.end for a, b in zip(ordered, ordered[1:])):
                raise ValueError("usage periods for an ingredient/source must not overlap")
        return self


class IngredientForecast(Ingredient):
    expected_usage: float | None
    uncertainty_buffer: float | None
    method: Literal["same_weekday_daypart", "daypart_fallback", "insufficient_history"]
    source: Source
    sample_count: int
    backtest_count: int
    backtest_mae: float | None
    warnings: list[str]
    model: Literal["autoets", "historical", "unknown"] = "historical"
    baseline_backtest_mae: float | None = None
    autoets_backtest_mae: float | None = None


class ForecastResult(Contract):
    start: datetime
    end: datetime
    daypart: str
    forecasts: list[IngredientForecast]


class InventoryPosition(Ingredient):
    # Teammate computes this from usable stock, reservations, and batch eligibility.
    available_now: float = Field(ge=0)
    # Already accepted demand within this forecast period, not yet deducted from stock.
    committed_in_horizon: float = Field(ge=0)


class InventorySnapshot(Contract):
    version: str = Field(min_length=1)
    as_of: datetime
    # Must cover the whole forecast horizon without relying on a mid-window delivery.
    valid_until: datetime
    positions: list[InventoryPosition]
    commitments_known: bool = True
    version_is_authoritative: bool = True

    @model_validator(mode="after")
    def valid_snapshot(self):
        if self.valid_until <= self.as_of:
            raise ValueError("inventory validity must follow as_of")
        ids = [p.ingredient_id for p in self.positions]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate inventory ingredient")
        return self


class Requirement(Ingredient):
    quantity: float = Field(gt=0)


class Restriction(Contract):
    kind: Literal["allergy", "dietary"]
    value: str = Field(min_length=1)
    staff_review_note: str = Field(default="", max_length=2000)

    @model_validator(mode="after")
    def ensure_review_note(self):
        if not self.staff_review_note.strip():
            self.staff_review_note = f"Staff review required for {self.kind}: {self.value}."
        return self


class IngredientProfile(Ingredient):
    # Only verified restaurant/catalog data may set this flag. Never model output.
    verified: bool = False
    # True means the allergen is present; False means its absence was verified.
    allergen_status: dict[str, bool | None] = Field(default_factory=dict)
    # True means the ingredient meets the named requirement (e.g. vegan).
    dietary_status: dict[str, bool | None] = Field(default_factory=dict)


class Candidate(Contract):
    option_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    requirements: list[Requirement] = Field(default_factory=list)
    approved: bool
    # Explicit attestations from interpretation/recipe/restaurant services.
    customer_constraints_met: bool | None
    preparation_feasible: bool | None
    requires_staff_review: bool = False
    similarity: float | None = Field(default=None, ge=0, le=1)
    similarity_source: Literal["inventory", "supplied", "not_applicable", "unavailable"] = "inventory"
    similarity_rationale: str | None = None
    culinary_description: str | None = None
    dish_id: str | None = None
    changes: dict[str, Any] = Field(default_factory=dict)
    inventory_check_passed: bool | None = True
    inventory_reasons: list[str] = Field(default_factory=list)
    staff_review_notes: list[str] = Field(default_factory=list)
    ingredient_cost_change_cents: int
    additional_labor_cents: int = Field(ge=0)
    price_change_cents: int

    @model_validator(mode="after")
    def unique_requirements(self):
        ids = [r.ingredient_id for r in self.requirements]
        if len(ids) != len(set(ids)):
            raise ValueError("aggregate recipe requirements by ingredient before evaluation")
        return self


class RankingPolicy(Contract):
    similarity_weight: float = Field(default=0.55, ge=0)
    reserve_weight: float = Field(default=0.35, ge=0)
    cost_weight: float = Field(default=0.10, ge=0)
    # Dollars/cents scale for the illustrative normalized cost score.
    cost_scale_cents: int = Field(default=500, gt=0)

    @model_validator(mode="after")
    def nonzero_weights(self):
        if self.similarity_weight + self.reserve_weight + self.cost_weight <= 0:
            raise ValueError("at least one ranking weight must be positive")
        return self


class EvaluationRequest(Contract):
    schema_version: Literal["1.0"] = "1.0"
    evaluated_at: datetime
    forecast: ForecastRequest
    inventory: InventorySnapshot
    candidates: list[Candidate] = Field(min_length=1)
    policy: RankingPolicy = Field(default_factory=RankingPolicy)
    max_snapshot_age_seconds: int = Field(default=300, ge=1)
    restrictions: list[Restriction] = Field(default_factory=list)
    ingredient_profiles: list[IngredientProfile] = Field(default_factory=list)
    staff_review_notes: list[str] = Field(default_factory=list)
    culinary_context: str | None = None

    @model_validator(mode="after")
    def valid_evaluation(self):
        if self.evaluated_at != self.forecast.start:
            raise ValueError("v1 evaluates at the start of the forecast window")
        ids = [c.option_id for c in self.candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate option_id")
        if any(not c.requirements for c in self.candidates):
            raise ValueError("evaluation requires the complete customized recipe for every candidate")
        profile_ids = [p.ingredient_id for p in self.ingredient_profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("duplicate ingredient profile")
        return self


class IngredientTradeoff(Ingredient):
    requested_quantity: float
    available_after_commitments: float
    uncommitted_expected_demand: float | None
    uncertainty_buffer: float | None
    projected_surplus: float | None
    reserve_score: float | None


class DecisionFact(Contract):
    fact_id: str
    text: str


class RankedOption(Contract):
    option_id: str
    label: str
    score: float | None
    eligible: bool
    requires_staff_review: bool
    reasons: list[str]
    tradeoffs: list[IngredientTradeoff]
    ingredient_cost_change_cents: int
    additional_labor_cents: int
    price_change_cents: int
    factors: dict[str, float]
    facts: list[DecisionFact]
    explanation: str
    staff_review_notes: list[str] = Field(default_factory=list)
    similarity: float | None = None
    similarity_source: Literal["inventory", "supplied", "not_applicable", "unavailable"] = "inventory"
    similarity_rationale: str | None = None
    blocked_by_restriction: bool = False


class EvaluationResult(Contract):
    schema_version: Literal["1.0"] = "1.0"
    inventory_version: str
    status: Literal["needs_customer_approval", "needs_staff_review", "unavailable"]
    recommended_option_id: str | None
    forecasts: ForecastResult
    ranked_options: list[RankedOption]
    excluded_options: list[RankedOption]
    review_options: list[RankedOption] = Field(default_factory=list)
    staff_review_notes: list[str] = Field(default_factory=list)
    explanation_source: Literal["deterministic", "openrouter"] = "deterministic"
    warnings: list[str] = Field(default_factory=list)
