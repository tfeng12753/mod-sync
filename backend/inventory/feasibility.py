"""Apply parsed modification ops to a dish and decide whether the result can be made.

Deterministic rules only: prep stage, approved extras and swaps, allergens, diet, and stock.
Every outcome is an explicit status with reasons (fail closed).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction
from typing import Any, Iterable

from .catalog import Catalog
from .models import OPS, STATUS_PRECEDENCE, CheckResult, Reason
from .stock import StockStore

HALF = Fraction(1, 2)


@dataclass
class Part:
    """One component as it will appear in the customized dish."""

    component: str
    servings: Fraction
    factor: Fraction = Fraction(1)
    removed: set[str] = field(default_factory=set)
    ingredient_factor: dict[str, Fraction] = field(default_factory=dict)
    on_side: bool = False
    replaced_from: str | None = None
    extra: int = 0


@dataclass(frozen=True)
class Op:
    kind: str
    target: str
    qty: int = 1
    with_: str | None = None


# ----- public entry point ------------------------------------------------------------------------


def evaluate(
    catalog: Catalog,
    stock: StockStore,
    dish_id: str,
    ops: Iterable[dict[str, Any]] | None = None,
    qty: int = 1,
    exclude_allergens: Iterable[str] = (),
    require_diet: Iterable[str] = (),
) -> CheckResult:
    exclude = sorted({a.strip().lower() for a in exclude_allergens if a and a.strip()})
    diets = sorted({d.strip().lower() for d in require_diet if d and d.strip()})
    review = bool(exclude or diets)

    if dish_id not in catalog.dishes:
        return _early(dish_id, qty, review, Reason("UNKNOWN_DISH", f"Unknown dish {dish_id!r}.", "needs_clarification", dish_id))
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
        return _early(dish_id, 1, review, Reason("INVALID_QTY", f"Quantity must be a whole number >= 1, got {qty!r}.", "needs_clarification"))

    ctx = _Ctx(catalog, catalog.dishes[dish_id])
    ops_ok = ctx.normalize(list(ops or []))
    for op in ctx.drop_conflicts(ops_ok):
        ctx.apply(op)

    requirements = _requirements(catalog, ctx.parts, qty)
    standard = _requirements(catalog, _base_parts(ctx.dish), qty)
    cost_delta = round(_cost(catalog, requirements) - _cost(catalog, standard))

    ingredients = _final_ingredients(catalog, ctx.parts)
    allergen_sources = _allergen_sources(catalog, ingredients)
    for a in exclude:
        if a in allergen_sources:
            ctx.reason("ALLERGEN_PRESENT", f"Contains {a}: {', '.join(allergen_sources[a])}.", "blocked", a)
    for d in diets:
        offenders = [f"{catalog.name_of(i)} (in {catalog.name_of(c)})" for i, c in ingredients if d not in catalog.ingredients[i].diet]
        if offenders:
            ctx.reason("DIET_CONFLICT", f"Not {d}: {', '.join(offenders)}.", "blocked", d)

    for item, need in requirements.items():
        name = catalog.name_of(item)
        if not stock.is_tracked(item):
            ctx.reason("OUT_OF_STOCK", f"{name} is not tracked in stock.", "out_of_stock", item)
            continue
        avail = stock.available(item)
        buffer = stock.safety_buffer(item)
        if need > avail:
            ctx.reason("OUT_OF_STOCK", f"Need {need} {_unit(catalog, item)} of {name}, only {max(avail, 0)} available.", "out_of_stock", item)
        elif need > avail - buffer:
            ctx.reason("LOW_STOCK", f"{name} would dip into its safety buffer ({avail} available, keeping {buffer} in reserve).", "out_of_stock", item)

    instructions = list(ctx.instructions)
    if exclude:
        instructions = [f"ALLERGY: {', '.join(exclude)}"] + list(catalog.allergy_protocol) + instructions
    if diets:
        instructions = [f"DIET: {', '.join(diets)}"] + instructions

    evidence = {
        "components": [_component_evidence(catalog, p, qty) for p in ctx.parts.values()],
        "allergens_present": {a: srcs for a, srcs in sorted(allergen_sources.items())},
        "cross_contact": [f"{catalog.name_of(p.component)}: {note}" for p in ctx.parts.values()
                          for note in catalog.components[p.component].cross_contact],
        "excluded_allergens": exclude,
        "required_diet": diets,
    }
    if review:
        evidence["allergy_protocol"] = list(catalog.allergy_protocol)
        evidence["note"] = ("Checked against listed ingredients only. Cross-contact is not verified "
                            "by the system; staff must confirm before this is approved.")

    return CheckResult(
        status=_status(ctx.reasons),
        dish=dish_id,
        qty=qty,
        reasons=ctx.reasons,
        requirements=requirements,
        price_delta_cents=ctx.price_delta * qty,
        cost_delta_cents=cost_delta,
        kitchen_instructions=instructions,
        evidence=evidence,
        requires_staff_review=review,
    )


TAG_SIMILARITY_FLOOR = 0.2  # an approved swap with no shared tags still scores above zero


def similarity(catalog: Catalog, replace: str, with_: str, chef_score: float | None) -> tuple[float, str]:
    """Culinary fit on a 0-1 scale (deterministic, no LLM).

    Chef-entered score if present (source "chef"), else the Jaccard overlap of the two
    components' tags mapped onto [0.2, 1.0] and rounded to 2 decimals (source "tags").
    """
    if chef_score is not None:
        return float(chef_score), "chef"
    a = set(catalog.components[replace].tags)
    b = set(catalog.components[with_].tags)
    if not a or not b:
        return TAG_SIMILARITY_FLOOR, "tags"
    jaccard = len(a & b) / len(a | b)
    return round(TAG_SIMILARITY_FLOOR + (1 - TAG_SIMILARITY_FLOOR) * jaccard, 2), "tags"


# ----- op handling -------------------------------------------------------------------------------


class _Ctx:
    def __init__(self, catalog: Catalog, dish) -> None:
        self.cat = catalog
        self.dish = dish
        self.parts: dict[str, Part] = _base_parts(dish)
        self.reasons: list[Reason] = []
        self.instructions: list[str] = []
        self.price_delta = 0

    def reason(self, code: str, message: str, status: str, item: str | None = None) -> None:
        self.reasons.append(Reason(code, message, status, item))

    def name(self, item_id: str) -> str:
        return self.cat.name_of(item_id)

    # normalization / conflicts

    def normalize(self, raw_ops: list[Any]) -> list[Op]:
        out = []
        for raw in raw_ops:
            if not isinstance(raw, dict):
                self.reason("INVALID_OP", f"Op must be an object, got {raw!r}.", "needs_clarification")
                continue
            kind = raw.get("op")
            target = raw.get("ingredient", raw.get("target", raw.get("component")))
            if kind not in OPS:
                self.reason("UNKNOWN_OP", f"Unknown op {kind!r}; expected one of {', '.join(OPS)}.", "needs_clarification", target)
                continue
            if not isinstance(target, str) or not target:
                self.reason("INVALID_OP", f"Op {kind!r} is missing an ingredient.", "needs_clarification")
                continue
            if not self._known(target):
                self.reason("UNKNOWN_ITEM", _unknown_message(target), "needs_clarification", target)
                continue
            qty = raw.get("qty", 1)
            if kind == "add" and (not isinstance(qty, int) or isinstance(qty, bool) or qty < 1):
                self.reason("INVALID_QTY", f"Extra quantity for {target!r} must be a whole number >= 1.", "needs_clarification", target)
                continue
            with_ = raw.get("with")
            if kind == "substitute" and with_ is not None and not self._known(with_):
                self.reason("UNKNOWN_ITEM", _unknown_message(with_), "needs_clarification", with_)
                continue
            out.append(Op(kind, target, int(qty) if kind == "add" else 1, with_))
        return out

    def drop_conflicts(self, ops: list[Op]) -> list[Op]:
        by_key: dict[str, set[str]] = {}
        for op in ops:
            by_key.setdefault(self._conflict_key(op.target), set()).add(op.kind)
        bad = {k for k, kinds in by_key.items()
               if ("remove" in kinds and len(kinds) > 1) or ("substitute" in kinds and kinds & {"add", "less"})}
        for k in sorted(bad):
            self.reason("CONFLICTING_OPS",
                        f"Conflicting requests for {self.name(k)} ({', '.join(sorted(by_key[k]))}); ask the customer which they meant.",
                        "needs_clarification", k)
        return [op for op in ops if self._conflict_key(op.target) not in bad]

    def apply(self, op: Op) -> None:
        getattr(self, f"_op_{op.kind}")(op)

    # individual ops

    def _op_remove(self, op: Op) -> None:
        hits = self._find(op.target)
        if not hits:
            self.reason("NOT_IN_DISH", f"{self.dish.name} doesn't contain {self.name(op.target)}; nothing to remove.", "info", op.target)
            return
        if self._block_premade(op, hits, "left out"):
            return
        for cid, how in hits:
            if how in ("component", "main"):
                self.parts.pop(cid, None)
                self.instructions.append(f"NO {self.name(cid)}")
            else:
                self.parts[cid].removed.add(op.target)
                self.instructions.append(f"NO {self.name(op.target)} in {self.name(cid)}")

    def _op_add(self, op: Op) -> None:
        cid = self._extra_component(op.target)
        if cid is None:
            if any(how == "premade" for _, how in self._find(op.target)):
                self._block_premade(op, self._find(op.target), "added separately")
            else:
                self.reason("EXTRA_NOT_OFFERED", f"Extra {self.name(op.target)} isn't offered on {self.dish.name}.", "blocked", op.target)
            return
        extra = self.dish.extras[cid]
        part = self.parts.get(cid)
        already = part.extra if part else 0
        if already + op.qty > extra.max:
            self.reason("EXTRA_OVER_MAX", f"At most {extra.max} extra {self.name(cid)} allowed on {self.dish.name}.", "blocked", cid)
            return
        if part is None:
            part = self.parts[cid] = Part(cid, Fraction(0))
        part.servings += op.qty
        part.extra += op.qty
        self.price_delta += extra.price_cents * op.qty
        self.instructions.append(f"EXTRA {self.name(cid)} x{op.qty}")

    def _op_substitute(self, op: Op) -> None:
        if op.with_ is None:
            self.reason("SUBSTITUTE_TARGET_MISSING",
                        f"Customer wants to swap {self.name(op.target)} but didn't say for what; offer candidates.",
                        "needs_clarification", op.target)
            return
        hits = self._find(op.target)
        if not hits:
            self.reason("NOT_IN_DISH", f"{self.dish.name} doesn't contain {self.name(op.target)}, so it can't be swapped.", "needs_clarification", op.target)
            return
        if self._block_premade(op, hits, "swapped on its own"):
            return
        whole = [cid for cid, how in hits if how in ("component", "main")]
        if not whole:
            self.reason("SUBSTITUTE_NOT_APPROVED", f"{self.name(op.target)} can't be swapped on its own in {self.dish.name}.", "blocked", op.target)
            return
        cid = whole[0]
        sub = self._approved(cid, op.with_)
        if sub is None:
            self.reason("SUBSTITUTE_NOT_APPROVED",
                        f"Swapping {self.name(cid)} for {self.name(op.with_)} isn't chef-approved for {self.dish.name}.",
                        "blocked", cid)
            return
        old = self.parts[cid]
        new = Part(sub.with_, old.servings, old.factor, on_side=old.on_side, replaced_from=cid, extra=old.extra)
        rebuilt: dict[str, Part] = {}
        for k, p in self.parts.items():
            if k == cid:
                if sub.with_ in self.parts:
                    continue
                rebuilt[sub.with_] = new
            elif k == sub.with_:
                p.servings += new.servings
                rebuilt[k] = p
            else:
                rebuilt[k] = p
        self.parts = rebuilt
        self.price_delta += sub.price_delta_cents
        self.instructions.append(f"SUB {self.name(sub.with_)} for {self.name(cid)}")

    def _op_on_side(self, op: Op) -> None:
        hits = self._find(op.target)
        if not hits:
            self.reason("NOT_IN_DISH", f"{self.dish.name} doesn't contain {self.name(op.target)}.", "info", op.target)
            return
        if self._block_inseparable(op, hits, "served on the side"):
            return
        for cid, _ in hits:
            self.parts[cid].on_side = True
            self.instructions.append(f"{self.name(cid)} ON THE SIDE")

    def _op_less(self, op: Op) -> None:
        hits = self._find(op.target)
        if not hits:
            self.reason("NOT_IN_DISH", f"{self.dish.name} doesn't contain {self.name(op.target)}.", "info", op.target)
            return
        if self._block_premade(op, hits, "reduced on its own"):
            return
        for cid, how in hits:
            if how in ("component", "main"):
                self.parts[cid].factor = HALF
                self.instructions.append(f"LIGHT {self.name(cid)}")
            else:
                self.parts[cid].ingredient_factor[op.target] = HALF
                self.instructions.append(f"LIGHT {self.name(op.target)} in {self.name(cid)}")

    # helpers

    def _known(self, item_id: str) -> bool:
        return item_id in self.cat.ingredients or item_id in self.cat.components

    def _find(self, target: str) -> list[tuple[str, str]]:
        """Where ``target`` appears in the current dish: component / main / part / premade."""
        if target in self.cat.components:
            return [(target, "component")] if target in self.parts else []
        hits = []
        for cid, part in self.parts.items():
            comp = self.cat.components[cid]
            if comp.is_premade:
                if target in comp.contains:
                    hits.append((cid, "premade"))
            elif target in comp.uses and target not in part.removed:
                hits.append((cid, "main" if comp.main == target else "part"))
        return hits

    def _conflict_key(self, target: str) -> str:
        if target in self.cat.components:
            return target
        mains = [cid for cid, how in self._find(target) if how == "main"]
        return mains[0] if mains else target

    def _block_premade(self, op: Op, hits: list[tuple[str, str]], verb: str) -> bool:
        premade = [cid for cid, how in hits if how == "premade"]
        for cid in premade:
            swaps = [self.name(s.with_) for s in self.dish.substitutions if s.replace == cid]
            hint = f" Approved swaps for {self.name(cid)}: {', '.join(swaps)}." if swaps else ""
            self.reason("PREMADE_INGREDIENT",
                        f"{self.name(op.target)} is mixed into {self.name(cid)}, which is made ahead, so it can't be {verb}. "
                        f"Remove or swap the whole {self.name(cid)} instead.{hint}",
                        "blocked", op.target)
        return bool(premade)

    def _block_inseparable(self, op: Op, hits: list[tuple[str, str]], verb: str) -> bool:
        if self._block_premade(op, hits, verb):
            return True
        parts = [cid for cid, how in hits if how == "part"]
        for cid in parts:
            self.reason("CANNOT_SEPARATE", f"{self.name(op.target)} is part of {self.name(cid)} and can't be {verb}.", "blocked", op.target)
        return bool(parts)

    def _extra_component(self, target: str) -> str | None:
        if target in self.cat.components:
            return target if target in self.dish.extras else None
        for cid in self.dish.extras:
            if self.cat.components[cid].main == target:
                return cid
        return None

    def _approved(self, cid: str, with_: str):
        options = [s for s in self.dish.substitutions if s.replace == cid]
        if with_ in self.cat.components:
            return next((s for s in options if s.with_ == with_), None)
        return next((s for s in options if self.cat.components[s.with_].main == with_), None)


# ----- computations ------------------------------------------------------------------------------


def _base_parts(dish) -> dict[str, Part]:
    return {cid: Part(cid, Fraction(s)) for cid, s in dish.components.items()}


def _requirements(catalog: Catalog, parts: dict[str, Part], qty: int) -> dict[str, int]:
    totals: dict[str, Fraction] = {}
    for p in parts.values():
        comp = catalog.components[p.component]
        scale = p.servings * p.factor * qty
        if comp.is_premade:
            totals[comp.id] = totals.get(comp.id, Fraction(0)) + comp.portion * scale
        else:
            for ing, amount in comp.uses.items():
                if ing in p.removed:
                    continue
                totals[ing] = totals.get(ing, Fraction(0)) + amount * scale * p.ingredient_factor.get(ing, Fraction(1))
    return {k: math.ceil(v) for k, v in totals.items() if v > 0}


def _cost(catalog: Catalog, requirements: dict[str, int]) -> float:
    return sum(q * catalog.unit_cost(item) for item, q in requirements.items())


def _final_ingredients(catalog: Catalog, parts: dict[str, Part]) -> list[tuple[str, str]]:
    out = []
    for p in parts.values():
        comp = catalog.components[p.component]
        for ing in comp.ingredient_ids():
            if ing not in p.removed:
                out.append((ing, comp.id))
    return out


def _allergen_sources(catalog: Catalog, ingredients: list[tuple[str, str]]) -> dict[str, list[str]]:
    sources: dict[str, list[str]] = {}
    for ing, cid in ingredients:
        for a in catalog.ingredients[ing].allergens:
            sources.setdefault(a, []).append(f"{catalog.name_of(ing)} (in {catalog.name_of(cid)})")
    return sources


def _component_evidence(catalog: Catalog, p: Part, qty: int) -> dict[str, Any]:
    comp = catalog.components[p.component]
    contents = [i for i in comp.ingredient_ids() if i not in p.removed]
    diet_sets = [set(catalog.ingredients[i].diet) for i in contents]
    return {
        "id": comp.id,
        "name": comp.name,
        "prep": comp.prep,
        "servings": float(p.servings * p.factor * qty),
        "ingredient_ids": contents,
        "contents": [catalog.name_of(i) for i in contents],
        "removed": [catalog.name_of(i) for i in sorted(p.removed)],
        "allergens": sorted({a for i in contents for a in catalog.ingredients[i].allergens}),
        "diet": sorted(set.intersection(*diet_sets)) if diet_sets else [],
        "cross_contact": list(comp.cross_contact),
        "on_side": p.on_side,
        "replaced_from": p.replaced_from,
    }


def _unit(catalog: Catalog, item: str) -> str:
    if item in catalog.components:
        return catalog.components[item].unit or ""
    return catalog.ingredients[item].unit if item in catalog.ingredients else ""


def _unknown_message(item_id: str) -> str:
    word = item_id.split(":", 1)[1] if item_id.startswith("unknown:") else item_id
    word = word.replace("_", " ")
    return f"We don't have \"{word}\" on our menu. Could you choose something else, or leave it out?"


def _status(reasons: list[Reason]) -> str:
    present = {r.status for r in reasons}
    return next((s for s in STATUS_PRECEDENCE if s in present), "ok")


def _early(dish_id: str, qty: int, review: bool, reason: Reason) -> CheckResult:
    return CheckResult(status=reason.status, dish=dish_id, qty=qty, reasons=[reason], requirements={},
                       requires_staff_review=review)
