"""One-call entry point: parser output in, everything the API/UI needs out.

    evaluation = inv.evaluate_request(parser_output)

1. Check the request exactly as asked.
2. If it can't be made, work out which dish components are the problem (allergen, diet,
   out of stock, unspecified or unapproved swap, ingredient locked inside a premade component).
3. Try every combination of chef-approved swaps for those components (and "leave it off" for
   components marked ``can_leave_off``), check each combination in full, keep the ones that pass.
4. Pick a status and a recommendation. Forecast may re-rank ``options`` afterwards.
"""

from __future__ import annotations

from itertools import product
from typing import TYPE_CHECKING, Any

from .feasibility import similarity
from .models import Option, Reason, RequestEvaluation

if TYPE_CHECKING:
    from .service import Inventory

MAX_COMBINATIONS = 64

# Reason codes an alternative option might fix, and how to find the component(s) to change.
_BY_OP_TARGET = {"SUBSTITUTE_TARGET_MISSING", "SUBSTITUTE_NOT_APPROVED", "PREMADE_INGREDIENT"}
_BY_EVIDENCE = {"ALLERGEN_PRESENT", "DIET_CONFLICT", "OUT_OF_STOCK", "LOW_STOCK"}


def evaluate_request(inv: "Inventory", parser_output: dict[str, Any]) -> RequestEvaluation:
    dish_id = parser_output.get("dish")
    qty = parser_output.get("qty", 1)
    ops = list(parser_output.get("ops") or [])
    flags = parser_output.get("flags") or {}
    exclude = [a.lower() for a in flags.get("allergens") or []]
    raw_diets = flags.get("diets", flags.get("diet")) or []
    diets = [d.lower() for d in ([raw_diets] if isinstance(raw_diets, str) else raw_diets)]
    level = (flags.get("level") or "").lower()
    review = bool(exclude or diets or level in ("allergy", "diet"))
    review_reasons = [f"allergy: {a}" for a in exclude] + [f"diet: {d}" for d in diets]
    if review and not review_reasons:
        review_reasons = [f"{level} flagged but not specified"]

    first = inv.check(dish_id, ops, qty, exclude, diets)

    def result(status, options=(), reasons=(), recommended=None, requirements=None, base=None):
        return RequestEvaluation(
            status=status, dish=dish_id, qty=first.qty, original_text=parser_output.get("original_text", ""),
            requires_staff_review=review, review_reasons=review_reasons, check=first, options=list(options),
            reasons=list(reasons), recommended=recommended,
            requirements=dict(requirements if requirements is not None else first.requirements),
            base_ops=[dict(op) for op in (ops if base is None else base) if isinstance(op, dict)],
        )

    if first.ok:
        return result("needs_staff_review" if review else "ready")

    dish = inv.catalog.dishes.get(dish_id)
    problems = [r for r in first.reasons if r.status != "info"]
    if dish is None:
        return result("needs_clarification", reasons=problems, requirements={})

    targets: set[str] = set()
    unfixable: list[Reason] = []
    for r in problems:
        found = _targets_for(inv, dish, first, r)
        if found:
            targets |= found
        else:
            unfixable.append(r)

    if unfixable:
        status = "needs_clarification" if any(r.status == "needs_clarification" for r in unfixable) else "unavailable"
        return result(status, reasons=unfixable, requirements={})

    base_ops = [op for op in ops if not _replaced_by_options(inv, dish, op, targets, first)]
    options = _options(inv, dish, sorted(targets), base_ops, qty, exclude, diets)
    if not options:
        reason = Reason("NO_FEASIBLE_OPTION",
                        f"No approved alternative for {', '.join(inv.catalog.name_of(t) for t in sorted(targets))} works right now.",
                        "blocked")
        return result("unavailable", reasons=problems + [reason], requirements={}, base=base_ops)

    return result("needs_staff_review" if review else "needs_customer_approval",
                  options=options, recommended=0, requirements=options[0].requirements, base=base_ops)


# ----- helpers -----------------------------------------------------------------------------------


def _fixable_component(inv, dish, cid: str) -> bool:
    has_swap = any(s.replace == cid for s in dish.substitutions)
    return has_swap or (cid in dish.components and inv.catalog.components[cid].can_leave_off)


def _components_for_target(inv, dish, target: str) -> set[str]:
    """Original dish components an op target refers to."""
    cat = inv.catalog
    if target in cat.components:
        return {target} if target in dish.components else set()
    out = set()
    for cid in dish.components:
        comp = cat.components[cid]
        if (comp.is_premade and target in comp.contains) or (not comp.is_premade and comp.main == target):
            out.add(cid)
    return out


def _targets_for(inv, dish, check, reason: Reason) -> set[str]:
    if reason.code in _BY_OP_TARGET and reason.item:
        found = _components_for_target(inv, dish, reason.item)
        if reason.code == "PREMADE_INGREDIENT":
            found = {c for c in found if inv.catalog.components[c].is_premade}
    elif reason.code in _BY_EVIDENCE and reason.item:
        found = set()
        for comp in check.evidence.get("components", []):
            hit = {
                "ALLERGEN_PRESENT": reason.item in comp["allergens"],
                "DIET_CONFLICT": reason.item not in comp["diet"],
            }.get(reason.code, comp["id"] == reason.item or reason.item in comp["ingredient_ids"])
            if hit:
                found.add(comp["replaced_from"] or comp["id"])
        found &= set(dish.components)
    else:
        return set()
    found = {c for c in found if _fixable_component(inv, dish, c)}
    return found


def _replaced_by_options(inv, dish, op: dict[str, Any], targets: set[str], check) -> bool:
    """Drop the customer's own swap of a target (options replace it) and ops that were blocked
    because the ingredient is locked inside a target premade component."""
    target = op.get("ingredient", op.get("target", op.get("component")))
    if not isinstance(target, str):
        return False
    comps = _components_for_target(inv, dish, target)
    if op.get("op") == "substitute" and comps & targets:
        return True
    premade_blocked = {r.item for r in check.reasons if r.code == "PREMADE_INGREDIENT"}
    return target in premade_blocked and bool(comps & targets)


def _options(inv, dish, targets: list[str], base_ops, qty, exclude, diets) -> list[Option]:
    cat = inv.catalog
    choices = []
    for t in targets:
        alts = []
        for s in dish.substitutions:
            if s.replace == t:
                score, source = similarity(cat, s.replace, s.with_, s.similarity)
                alts.append({"replace": t, "with": s.with_, "similarity": score, "source": source})
        if cat.components[t].can_leave_off:
            # Leaving something off has no culinary similarity; it is ranked after swaps.
            alts.append({"replace": t, "with": None, "similarity": None, "source": "leave_off"})
        choices.append(alts)

    options = []
    for i, combo in enumerate(product(*choices)):
        if i >= MAX_COMBINATIONS:
            break
        extra_ops = [{"op": "substitute", "ingredient": c["replace"], "with": c["with"]} if c["with"]
                     else {"op": "remove", "ingredient": c["replace"]} for c in combo]
        option_ops = [dict(op) for op in base_ops] + extra_ops
        res = inv.check(dish.id, option_ops, qty, exclude, diets)
        if not res.ok:
            continue
        swaps = [c["similarity"] for c in combo if c["with"]]
        options.append(Option(
            changes=[dict(c) for c in combo],
            similarity=min(swaps) if swaps else None,  # weakest swap; None = only leaves things off
            price_delta_cents=res.price_delta_cents,
            cost_delta_cents=res.cost_delta_cents,
            requirements=dict(res.requirements),
            available={item: inv.stock.available(item) for item in res.requirements},
            check=res,
            ops=option_ops,
        ))
    leave_offs = lambda o: sum(c["with"] is None for c in o.changes)  # noqa: E731
    # Options with a swap first (highest weakest-swap similarity), then leave-off-only options.
    options.sort(key=lambda o: (o.similarity is None, -(o.similarity or 0.0), leave_offs(o),
                                o.price_delta_cents, o.cost_delta_cents))
    return options
