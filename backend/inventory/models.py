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
    similarity: int | None  # chef-entered 1-5, or None to fall back to tag overlap
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
    similarity: int
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
class StockResult:
    ok: bool
    code: str
    message: str
    shortfalls: dict[str, dict[str, int]] = field(default_factory=dict)
    already_confirmed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
