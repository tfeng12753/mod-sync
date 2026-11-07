"""Public facade for the inventory module. Other parts of the backend should only use this."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Iterable

from .catalog import Catalog, load_catalog
from .feasibility import evaluate, similarity
from .models import Candidate, CheckResult, StockResult
from .stock import Clock, StockStore, load_stock

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


class Inventory:
    def __init__(self, catalog: Catalog, stock: StockStore) -> None:
        self.catalog = catalog
        self.stock = stock

    # ----- feasibility -------------------------------------------------------------------------

    def check(
        self,
        dish_id: str,
        ops: Iterable[dict[str, Any]] | None = None,
        qty: int = 1,
        exclude_allergens: Iterable[str] = (),
        require_diet: Iterable[str] = (),
    ) -> CheckResult:
        return evaluate(self.catalog, self.stock, dish_id, ops, qty, exclude_allergens, require_diet)

    def requirements(self, dish_id: str, ops: Iterable[dict[str, Any]] | None = None, qty: int = 1) -> dict[str, int]:
        return self.check(dish_id, ops, qty).requirements

    def substitute_candidates(
        self,
        dish_id: str,
        target: str,
        qty: int = 1,
        exclude_allergens: Iterable[str] = (),
        require_diet: Iterable[str] = (),
        base_ops: Iterable[dict[str, Any]] = (),
        feasible_only: bool = True,
    ) -> list[Candidate]:
        """Chef-approved swaps for ``target`` (a component, or an ingredient in one), each checked
        together with the customer's other ops. Sorted by similarity; forecast does final ranking."""
        if dish_id not in self.catalog.dishes:
            raise KeyError(f"unknown dish {dish_id!r}")
        dish = self.catalog.dishes[dish_id]
        targets = self._swappable(dish_id, target)
        other_ops = [op for op in base_ops if _op_target(op) != target and _op_target(op) not in targets]
        exclude = list(exclude_allergens)
        diets = list(require_diet)

        out = []
        for sub in dish.substitutions:
            if sub.replace not in targets:
                continue
            ops = other_ops + [{"op": "substitute", "ingredient": sub.replace, "with": sub.with_}]
            result = self.check(dish_id, ops, qty, exclude, diets)
            score, source = similarity(self.catalog, sub.replace, sub.with_, sub.similarity)
            out.append(Candidate(
                dish=dish_id,
                replace=sub.replace,
                with_=sub.with_,
                similarity=score,
                similarity_source=source,
                price_delta_cents=result.price_delta_cents,
                cost_delta_cents=result.cost_delta_cents,
                requirements=dict(result.requirements),
                available={item: self.stock.available(item) for item in result.requirements},
                check=result,
            ))
        if feasible_only:
            out = [c for c in out if c.feasible]
        return sorted(out, key=lambda c: -c.similarity)

    # ----- stock -------------------------------------------------------------------------------

    def available(self, item_id: str) -> int:
        return self.stock.available(item_id)

    def hold(self, order_id: str, requirements: dict[str, int], ttl_seconds: float = 600) -> StockResult:
        return self.stock.hold(order_id, requirements, ttl_seconds)

    def confirm(self, order_id: str, requirements: dict[str, int] | None = None) -> StockResult:
        return self.stock.confirm(order_id, requirements)

    def release(self, order_id: str) -> bool:
        return self.stock.release(order_id)

    def expired_holds(self) -> list[str]:
        return self.stock.pop_expired()

    def adjust(self, item_id: str, delta: int, reason: str) -> int:
        return self.stock.adjust(item_id, delta, reason)

    def set_on_hand(self, item_id: str, qty: int, reason: str) -> int:
        return self.stock.set_on_hand(item_id, qty, reason)

    def usage_log(self) -> list[dict[str, Any]]:
        return self.stock.usage_log()

    def deliveries(self) -> list[dict[str, Any]]:
        return self.stock.deliveries()

    # ----- internals ---------------------------------------------------------------------------

    def _swappable(self, dish_id: str, target: str) -> set[str]:
        dish = self.catalog.dishes[dish_id]
        if target in self.catalog.components:
            return {target} if target in dish.components else set()
        out = set()
        for cid in dish.components:
            comp = self.catalog.components[cid]
            if (comp.is_premade and target in comp.contains) or (not comp.is_premade and comp.main == target):
                out.add(cid)
        return out


def _op_target(op: dict[str, Any]) -> str | None:
    return op.get("ingredient", op.get("target", op.get("component")))


def load_inventory(data_dir: str | Path | None = None, clock: Clock = time.time) -> Inventory:
    d = Path(data_dir) if data_dir else DATA_DIR
    return Inventory(load_catalog(d / "catalog.json"), load_stock(d / "stock.json", clock=clock))
