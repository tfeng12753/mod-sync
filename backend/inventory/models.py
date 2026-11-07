"""Data types for the inventory module. Plain dataclasses; no I/O and no LLM calls."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

OPS = ("remove", "add", "substitute", "on_side", "less")

# Check statuses, most severe first. A result takes the most severe status among its reasons.
STATUS_PRECEDENCE = ("needs_clarification", "blocked", "out_of_stock", "ok")


@dataclass(frozen=True)
class Ingredient:
    id: str
    name: str
    unit: str
    allergens: tuple[str, ...] = ()
    diet: tuple[str, ...] = ()
    cost_cents_per_unit: float = 0.0


@dataclass(frozen=True)
class Component:
    """A part of a dish.

    premade:  made ahead and stocked as itself (``portion`` units per serving). ``contains`` lists
              its ingredients for allergen/diet checks; they cannot be removed individually.
    assembly: built per order from raw ingredients in ``uses``; ``main`` is the defining ingredient.
    """

    id: str
    name: str
    prep: str  # "premade" | "assembly"
    uses: dict[str, int] = field(default_factory=dict)
    main: str | None = None
    unit: str | None = None
    portion: int = 0
    contains: tuple[str, ...] = ()
    cost_cents_per_unit: float = 0.0
    cross_contact: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    can_leave_off: bool = False  # the system may suggest dropping it (sauces, toppings), never bases/proteins

    @property
    def is_premade(self) -> bool:
        return self.prep == "premade"

    def ingredient_ids(self) -> tuple[str, ...]:
        return self.contains if self.is_premade else tuple(self.uses)


@dataclass(frozen=True)
class Extra:
    component: str
    max: int
    price_cents: int


@dataclass(frozen=True)
class Substitution:
    replace: str
    with_: str
    similarity: float | None  # chef-entered 0-1, or None to fall back to tag overlap
    price_delta_cents: int = 0


@dataclass(frozen=True)
class Dish:
    id: str
    name: str
    price_cents: int
    components: dict[str, int]
    extras: dict[str, Extra] = field(default_factory=dict)
    substitutions: tuple[Substitution, ...] = ()


@dataclass
class Reason:
    code: str
    message: str
    status: str  # one of STATUS_PRECEDENCE, or "info" (does not affect status)
    item: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class CheckResult:
    status: str
    dish: str | None
    qty: int
    reasons: list[Reason]
    requirements: dict[str, int]
    price_delta_cents: int = 0
    cost_delta_cents: int = 0
    kitchen_instructions: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    requires_staff_review: bool = False

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["reasons"] = [r.to_dict() for r in self.reasons]
        return out


@dataclass
class Candidate:
    """A substitute option sent from inventory to forecast for final ranking."""

    dish: str
    replace: str
    with_: str
    similarity: float  # 0-1 culinary fit, deterministic (chef-entered or tag overlap)
    similarity_source: str  # "chef" | "tags"
    price_delta_cents: int
    cost_delta_cents: int
    requirements: dict[str, int]
    available: dict[str, int]
    check: CheckResult

    @property
    def feasible(self) -> bool:
        return self.check.ok

    def to_dict(self) -> dict[str, Any]:
        return {
            "dish": self.dish,
            "replace": self.replace,
            "with": self.with_,
            "similarity": self.similarity,
            "similarity_source": self.similarity_source,
            "price_delta_cents": self.price_delta_cents,
            "cost_delta_cents": self.cost_delta_cents,
            "requirements": dict(self.requirements),
            "available": dict(self.available),
            "check": self.check.to_dict(),
        }


@dataclass
class Option:
    """One complete way to make the dish: zero or more swaps/removals applied on top of the
    customer's request. Produced by ``evaluate_request`` for forecast to rank."""

    # [{"replace", "with" (None = leave it off), "similarity" (0-1; None for a leave-off), "source"}]
    changes: list[dict[str, Any]]
    similarity: float | None  # weakest swap's similarity (0-1); None if the option only leaves things off
    price_delta_cents: int
    cost_delta_cents: int
    requirements: dict[str, int]
    available: dict[str, int]
    check: CheckResult
    ops: list[dict[str, Any]] = field(default_factory=list)  # full inventory ops that make this option

    def to_dict(self) -> dict[str, Any]:
        return {
            "changes": [dict(c) for c in self.changes],
            "similarity": self.similarity,
            "price_delta_cents": self.price_delta_cents,
            "cost_delta_cents": self.cost_delta_cents,
            "requirements": dict(self.requirements),
            "available": dict(self.available),
            "check": self.check.to_dict(),
            "ops": [dict(op) for op in self.ops],
        }


# evaluate_request statuses
REQUEST_STATUSES = ("ready", "needs_customer_approval", "needs_staff_review", "needs_clarification", "unavailable")


@dataclass
class RequestEvaluation:
    """Everything the API/UI needs for one parsed request, in one object."""

    status: str
    dish: str | None
    qty: int
    original_text: str
    requires_staff_review: bool
    review_reasons: list[str]
    check: CheckResult  # the request exactly as the customer asked
    options: list[Option]  # alternatives when the request can't be made as asked
    reasons: list[Reason]  # problems that options could not fix
    recommended: int | None  # index into options, or None (use the request as asked / nothing possible)
    requirements: dict[str, int]  # what to hold/confirm if the recommendation is accepted
    # The customer's ops every option keeps (an option's ops = base_ops + its own changes).
    base_ops: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "dish": self.dish,
            "qty": self.qty,
            "original_text": self.original_text,
            "requires_staff_review": self.requires_staff_review,
            "review_reasons": list(self.review_reasons),
            "check": self.check.to_dict(),
            "options": [o.to_dict() for o in self.options],
            "reasons": [r.to_dict() for r in self.reasons],
            "recommended": self.recommended,
            "requirements": dict(self.requirements),
            "base_ops": [dict(op) for op in self.base_ops],
        }


@dataclass
class StockResult:
    ok: bool
    code: str
    message: str
    shortfalls: dict[str, dict[str, int]] = field(default_factory=dict)
    already_confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
