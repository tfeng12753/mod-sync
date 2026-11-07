"""Inventory <-> forecast engine adapter.

    from backend.integration.engine_adapter import route_options
    evaluation = inv.evaluate_request(parser_output)
    routing = await route_options(inv, evaluation)          # never confirms or holds stock

Inventory (``backend/inventory``) decides what *can* be made: prep stage, allergens, diets,
chef-approved swaps and stock, and returns ``evaluation.options``. The engine
(``backend/engine``, Person 4) decides how each feasible option is *routed*: combined score
(0.55 similarity + 0.35 forecast reserve + 0.10 cost, cutoff 0.75), customer vs staff vs
unavailable, price references and expiry. This module translates between the two.

Mapping (see docs/integration-plan.md, "Engine changes"):

* ``changes`` passed to the engine's inventory functions is a dict
  ``{"ops": [...inventory ops...], "qty": n, "exclude_allergens": [...], "require_diet": [...]}``
  (a bare ops list is accepted too). ``requirements`` returns per-dish quantities
  (``check(qty=n).requirements / n``, so rounding of "less" with qty > 1 agrees with ``check``).
* Every inventory option becomes one engine candidate (``option_id = "opt-<index>"``).
  Options with at least one swap use ``action="substitute"``; options that only leave things off
  use ``action="omit"`` (no similarity requirement). The *primary* change (the weakest swap, or the
  first leave-off) gives the engine's single ``target_ingredient_id`` (premade component -> its own
  stock id, assembly component -> its main ingredient) and the ``offered_item_id``.
* Each offer carries explicit ``removed_requirements`` (what the changed components used) and
  ``portion_requirements`` (what the replacements use), both computed by inventory, with
  ``removed_requirements_verified=True``: inventory attests the removed quantities belong to the
  changed components (cooking oil of a replaced protein, a second leave-off in the same option).
  The engine still checks that the final recipe equals original - removed + portions exactly.
* Options sharing (action, target, exclude_target) go to one ``decide_modification`` call.
* Allergies/diets from the evaluation become engine ``restrictions`` with ``ingredient_profiles``
  generated from the catalog's allergen/diet tags (``verified=True``); they always route to staff.
* Forecast: synthetic usage history (``backend/data/usage_history.json``) for every item in the
  candidate recipes, window ``[now, horizon_end)`` with horizon end = min(now + 3 h, any item's
  use-by, any item's next delivery). History periods are 3 h dinners, so a shorter window yields
  "insufficient history" forecasts and the engine fails closed (staff review / unavailable).
* ``now`` defaults to ``DEMO_EVALUATION_TIME`` (2026-11-07T17:00-05:00), the Saturday dinner the
  synthetic history was generated for.

Nothing here calls ``confirm``/``hold``; the API does that after customer/staff acceptance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from backend.engine import (
    InventoryGateway,
    ModificationRequest,
    OutlookRequest,
    assess_extra,
    decide_modification,
    forecast_inventory,
)
from backend.engine.decisions import CatalogItem, DecisionPolicy, ModificationOffer
from backend.engine.integration import ConnectedEvaluationRequest
from backend.engine.schemas import Candidate, ForecastRequest, Ingredient, IngredientProfile, RankingPolicy, Restriction
from backend.engine.constraints import SAFETY_REVIEW_NOTE
from backend.inventory.history import DAYPART, DEMO_EVALUATION_TIME, DINNER_HOURS, SOURCE, TIME_ZONE

SUCCESS_STATUSES = {"ok"}
BLOCKED_STATUSES = {"blocked", "out_of_stock", "needs_clarification"}
FRESHNESS_SECONDS = 300  # engine default max_snapshot_age_seconds
NO_OPTION_MESSAGE = "We cannot accommodate this request with the available menu and inventory options."


# ----- public result types (the API builds its response from these) -----------------------------


@dataclass
class RoutedChoice:
    option_index: int | None  # index into evaluation.options; None = the request as asked
    route: str  # "customer" | "staff"
    label: str
    score: float | None  # engine combined score (unrounded); None for the request as asked / leave-offs
    similarity: float | None
    price_change_cents: int | None  # whole order; None = staff must set a price
    reasons: list[str] = field(default_factory=list)
    staff_notes: list[str] = field(default_factory=list)


@dataclass
class RoutingResult:
    status: str  # "reply_to_customer" | "needs_staff_review" | "unavailable"
    customer_message: str
    choices: list[RoutedChoice]  # best first: customer route, then staff route
    unavailable: list[dict[str, Any]]  # [{"option_index", "label", "reasons"}]
    staff_review_notes: list[str]
    expires_at: datetime
    forecast_summary: dict[str, dict[str, Any]]
    raw: list[dict[str, Any]]  # engine decision(s)/outlook model_dump(mode="json") for debugging


# ----- inventory functions for the engine's InventoryGateway --------------------------------------


def make_changes(ops: Iterable[Mapping[str, Any]], *, qty: int = 1, exclude_allergens: Iterable[str] = (),
                 require_diet: Iterable[str] = ()) -> dict[str, Any]:
    """The ``changes`` payload the engine passes back to our inventory functions."""
    return {"ops": [dict(op) for op in ops], "qty": int(qty),
            "exclude_allergens": list(exclude_allergens), "require_diet": list(require_diet)}


def _parse_changes(changes: Any) -> tuple[list[dict[str, Any]], int, list[str], list[str]]:
    if changes is None:
        return [], 1, [], []
    if isinstance(changes, (list, tuple)):
        return [dict(op) for op in changes], 1, [], []
    if isinstance(changes, Mapping):
        qty = changes.get("qty", 1)
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1:
            raise ValueError(f"changes qty must be a whole number >= 1, got {qty!r}")
        return ([dict(op) for op in changes.get("ops") or []], qty,
                list(changes.get("exclude_allergens") or []), list(changes.get("require_diet") or []))
    raise TypeError(f"changes must be an ops list or a dict with 'ops', got {type(changes).__name__}")


class InventoryFunctions:
    """Implements the engine's ``InventoryFunctions`` protocol over ``Inventory``."""

    def __init__(self, inv) -> None:
        self.inv = inv

    def requirements(self, dish_id: str, changes: Any) -> dict[str, float]:
        """Per-dish stock quantities for the customized dish."""
        ops, qty, _, _ = _parse_changes(changes)
        reqs = self.inv.check(dish_id, ops, qty).requirements
        return {item: (q if qty == 1 else q / qty) for item, q in reqs.items()}

    def check(self, dish_id: str, changes: Any, qty: int = 1) -> dict[str, Any]:
        """Full inventory check; ``qty`` is authoritative. Reasons become plain messages."""
        ops, _, exclude, diets = _parse_changes(changes)
        result = self.inv.check(dish_id, ops, qty, exclude, diets)
        return {
            "status": result.status,
            "reasons": [r.message for r in result.reasons],
            "requirements": dict(result.requirements),
            "requires_staff_review": result.requires_staff_review,
            "kitchen_instructions": list(result.kitchen_instructions),
        }

    def available(self, item_id: str) -> int:
        """On-hand minus active holds (never negative)."""
        return max(0, self.inv.available(item_id))

    def confirm(self, order: Mapping[str, Any]) -> dict[str, Any]:
        """``order = {"order_id": ..., "requirements": {...} | None}`` -> ``inv.confirm``.
        Only the API calls this, after customer/staff acceptance."""
        if not isinstance(order, Mapping) or not order.get("order_id"):
            raise ValueError("confirm(order) needs a mapping with an order_id")
        return self.inv.confirm(order["order_id"], order.get("requirements")).to_dict()


def inventory_functions(inv) -> InventoryFunctions:
    return InventoryFunctions(inv)


def make_gateway(inv) -> InventoryGateway:
    return InventoryGateway(inventory_functions(inv), success_statuses=set(SUCCESS_STATUSES),
                            blocked_statuses=set(BLOCKED_STATUSES))


# ----- catalog -> engine facts ---------------------------------------------------------------------


def _item_allergens(inv, item: str) -> set[str]:
    cat = inv.catalog
    if item in cat.components:
        return {a for i in cat.components[item].ingredient_ids() for a in cat.ingredients[i].allergens}
    return set(cat.ingredients[item].allergens) if item in cat.ingredients else set()


def _item_diets(inv, item: str) -> set[str]:
    cat = inv.catalog
    if item in cat.components:
        sets = [set(cat.ingredients[i].diet) for i in cat.components[item].ingredient_ids()]
        return set.intersection(*sets) if sets else set()
    return set(cat.ingredients[item].diet) if item in cat.ingredients else set()


def _catalog_items(inv) -> list[CatalogItem]:
    """Every stocked item (in_inventory) plus every dish component (on_menu), composition verified
    from the catalog. A component that shares its ID with a stocked ingredient (e.g. lettuce) is
    the same single-ingredient item and is listed once."""
    cat = inv.catalog
    items: dict[str, CatalogItem] = {}
    for item in inv.stocked_items():
        comp = cat.components.get(item)
        items[item] = CatalogItem(
            item_id=item, in_inventory=True, on_menu=comp is not None,
            contains_ingredients=list(comp.contains) if comp is not None and comp.is_premade else [item],
            composition_verified=True, can_serve_separately=True,
        )
    for cid, comp in cat.components.items():
        if cid in items:
            continue
        items[cid] = CatalogItem(
            item_id=cid, in_inventory=False, on_menu=True,
            contains_ingredients=list(comp.ingredient_ids()), composition_verified=True,
            can_serve_separately=True,
        )
    return list(items.values())


def _flags(evaluation) -> tuple[list[str], list[str]]:
    checks = [evaluation.check] + [o.check for o in evaluation.options]
    for check in checks:
        evidence = check.evidence or {}
        if "excluded_allergens" in evidence or "required_diet" in evidence:
            return list(evidence.get("excluded_allergens", [])), list(evidence.get("required_diet", []))
    return [], []


def _restrictions(evaluation, exclude: list[str], diets: list[str]) -> list[Restriction]:
    said = f" Customer said: {evaluation.original_text!r}." if evaluation.original_text else ""
    return ([Restriction(kind="allergy", value=a, staff_review_note=f"Staff review required for allergy: {a}.{said}")
             for a in exclude] +
            [Restriction(kind="dietary", value=d, staff_review_note=f"Staff review required for diet: {d}.{said}")
             for d in diets])


def _staff_notes(evaluation, restrictions: list[Restriction]) -> list[str]:
    """Top-level notes: inventory review reasons that are not already a restriction."""
    if not evaluation.requires_staff_review:
        return []
    covered = {f"allergy: {r.value}" if r.kind == "allergy" else f"diet: {r.value}" for r in restrictions}
    notes = [f"Inventory flagged for staff review: {r}." for r in evaluation.review_reasons if r not in covered]
    if notes and evaluation.original_text:
        notes.append(f"Customer said: {evaluation.original_text!r}.")
    return notes


def _profiles(inv, items: Iterable[str], exclude: list[str], diets: list[str]) -> list[IngredientProfile]:
    out = []
    for item in items:
        allergens, item_diets = _item_allergens(inv, item), _item_diets(inv, item)
        out.append(IngredientProfile(
            ingredient_id=item, unit=inv.unit_of(item), verified=True,
            allergen_status={a: a in allergens for a in exclude},
            dietary_status={d: d in item_diets for d in diets},
        ))
    return out


def _history_records(inv, history) -> list[dict[str, Any]]:
    if history is None:
        return inv.usage_history()
    return [h.model_dump(mode="json") if hasattr(h, "model_dump") else dict(h) for h in history]


def _forecast(inv, items: list[str], now: datetime, history: list[dict[str, Any]]) -> ForecastRequest:
    end = inv.horizon_end(items, now, now + timedelta(hours=DINNER_HOURS))
    wanted = set(items)
    return ForecastRequest(
        start=now, end=end, time_zone=TIME_ZONE, daypart=DAYPART, source=SOURCE,
        ingredients=[Ingredient(ingredient_id=i, unit=inv.unit_of(i)) for i in items],
        history=[r for r in history if r.get("ingredient_id") in wanted],
    )


def _committed(inv, items: Iterable[str]) -> dict[str, float]:
    """Active holds per item (accepted-but-unconfirmed orders), so the engine sees gross stock."""
    return {i: float(max(0, inv.stock.on_hand(i) - inv.available(i))) for i in items}


# ----- options -> engine requests --------------------------------------------------------------------


def _label(inv, changes: list[dict[str, Any]]) -> str:
    name = inv.catalog.name_of
    return "; ".join(f"{name(c['with'])} instead of {name(c['replace'])}" if c["with"]
                     else f"Leave off {name(c['replace'])}" for c in changes)


def _target_id(inv, component_id: str) -> str:
    comp = inv.catalog.components[component_id]
    return comp.id if comp.is_premade else comp.main


def _touches(inv, op: Mapping[str, Any], components: set[str]) -> bool:
    target = op.get("ingredient", op.get("target", op.get("component")))
    if not isinstance(target, str):
        return False
    if target in components:
        return True
    for cid in components:
        comp = inv.catalog.components[cid]
        if (comp.is_premade and target in comp.contains) or (not comp.is_premade and comp.main == target):
            return True
    return False


def _per_dish(reqs: Mapping[str, int], qty: int) -> dict[str, float]:
    return {i: (q if qty == 1 else q / qty) for i, q in reqs.items()}


def _diff(a: Mapping[str, float], b: Mapping[str, float]) -> dict[str, float]:
    return {i: q - b.get(i, 0) for i, q in a.items() if q - b.get(i, 0) > 1e-9}


def _contains(inv, item: str, target: str) -> bool:
    if item == target:
        return True
    comp = inv.catalog.components.get(item)
    return bool(comp and comp.is_premade and target in comp.contains)


@dataclass
class _Plan:
    index: int
    action: str
    target: str
    exclude_target: bool
    candidate: Candidate
    offer: ModificationOffer
    leave_off_only: bool


def _plan_option(inv, evaluation, index: int, exclude: list[str], diets: list[str]) -> _Plan:
    option = evaluation.options[index]
    dish = inv.catalog.dishes[evaluation.dish]
    qty = evaluation.qty
    changes = option.changes
    swaps = [c for c in changes if c["with"]]
    primary = min(swaps, key=lambda c: c["similarity"]) if swaps else changes[0]
    action = "substitute" if swaps else "omit"
    target = _target_id(inv, primary["replace"])

    changed = {c["replace"] for c in changes}
    base_ops = list(evaluation.base_ops)
    mid_ops = ([op for op in base_ops if not _touches(inv, op, changed)] +
               [{"op": "remove", "ingredient": cid} for cid in sorted(changed)])
    original = _per_dish(inv.check(dish.id, base_ops, qty).requirements, qty)
    final = _per_dish(option.requirements, qty)
    middle = _per_dish(inv.check(dish.id, mid_ops, qty).requirements, qty)
    removed = _diff(original, middle)
    portion = _diff(final, middle)

    offered = primary["with"]
    portion_quantity = portion_unit = None
    if offered is not None:
        comp = inv.catalog.components[offered]
        stock_id = offered if comp.is_premade else comp.main
        portion_quantity = portion.get(stock_id)
        portion_unit = inv.unit_of(stock_id) if portion_quantity is not None else None

    price = ref = None
    if swaps:
        subs = {(s.replace, s.with_): s for s in dish.substitutions}
        price = sum(max(0, subs[(c["replace"], c["with"])].price_delta_cents) for c in swaps)
        ref = ";".join(f"catalog:{dish.id}:{c['replace']}->{c['with']}" for c in swaps)

    still_has_target = any(_contains(inv, item, target) for item in final)
    candidate = Candidate(
        option_id=f"opt-{index}", label=_label(inv, changes), dish_id=dish.id,
        changes=make_changes(option.ops, qty=qty, exclude_allergens=exclude, require_diet=diets),
        approved=True,  # chef-approved swaps / can_leave_off components only
        customer_constraints_met=True,  # inventory's check (allergens, diets) passed for this option
        preparation_feasible=True,  # inventory's prep-stage rules passed
        requires_staff_review=evaluation.requires_staff_review,
        similarity=option.similarity, similarity_source="inventory" if swaps else "not_applicable",
        similarity_rationale=("chef-approved swap, " + ", ".join(
            f"{c['replace']}->{c['with']} {c['similarity']:.2f} ({c['source']})" for c in swaps)) if swaps else None,
        ingredient_cost_change_cents=option.cost_delta_cents, additional_labor_cents=0, price_change_cents=0,
    )
    offer = ModificationOffer(
        option_id=candidate.option_id, offered_item_id=offered, approved_for_dish=True,
        portion_quantity=portion_quantity, portion_unit=portion_unit, portion_requirements=portion,
        removed_requirements=removed or None, removed_requirements_verified=bool(removed),
        menu_price_change_cents=price, menu_price_reference=ref,
    )
    return _Plan(index, action, target, not (action == "substitute" and still_has_target),
                 candidate, offer, not swaps)


def build_modification_requests(inv, evaluation, *, now: datetime | None = None,
                                history=None) -> list[tuple[ModificationRequest, list[int]]]:
    """Engine requests for ``evaluation.options``, grouped by (action, target, exclude_target).
    Returns ``[(request, [option indexes]), ...]``. Read-only."""
    now = _check_now(now)
    exclude, diets = _flags(evaluation)
    plans = [_plan_option(inv, evaluation, i, exclude, diets) for i in range(len(evaluation.options))]
    items = sorted({i for o in evaluation.options for i in o.requirements} |
                   set(inv.check(evaluation.dish, evaluation.base_ops, evaluation.qty).requirements))
    forecast = _forecast(inv, items, now, _history_records(inv, history))
    restrictions = _restrictions(evaluation, exclude, diets)
    profiles = _profiles(inv, items, exclude, diets) if (exclude or diets) else []
    catalog = _catalog_items(inv)
    original_changes = make_changes(evaluation.base_ops, qty=evaluation.qty,
                                    exclude_allergens=exclude, require_diet=diets)
    groups: dict[tuple[str, str, bool], list[_Plan]] = {}
    for plan in plans:
        groups.setdefault((plan.action, plan.target, plan.exclude_target), []).append(plan)
    out = []
    for (action, target, exclude_target), members in groups.items():
        request = ModificationRequest(
            action=action, dish_id=evaluation.dish, target_ingredient_id=target, exclude_target=exclude_target,
            original_changes=original_changes,
            evaluation=ConnectedEvaluationRequest(
                evaluated_at=now, forecast=forecast, usable_until=forecast.end,
                candidates=[p.candidate for p in members], qty=evaluation.qty,
                restrictions=restrictions, ingredient_profiles=profiles,
                staff_review_notes=_staff_notes(evaluation, restrictions),
                culinary_context=f"Dish: {inv.catalog.dishes[evaluation.dish].name}",
                policy=RankingPolicy(), committed_in_horizon=_committed(inv, items),
                max_snapshot_age_seconds=FRESHNESS_SECONDS,
            ),
            catalog=catalog, offers=[p.offer for p in members], policy=DecisionPolicy(),
        )
        out.append((request, [p.index for p in members]))
    return out


# ----- routing -----------------------------------------------------------------------------------


def _check_now(now: datetime | None) -> datetime:
    now = now or DEMO_EVALUATION_TIME
    if now.utcoffset() is None:
        raise ValueError("now must include a UTC offset")
    return now


def _forecast_summary(inv, forecasts, committed: Mapping[str, float]) -> dict[str, dict[str, Any]]:
    out = {}
    for f in forecasts.forecasts:
        available = float(inv.available(f.ingredient_id))
        headroom = risk = None
        if f.expected_usage is not None and f.uncertainty_buffer is not None:
            remaining = max(0.0, f.expected_usage - committed.get(f.ingredient_id, 0.0))
            balance = max(0.0, available) - remaining
            headroom = balance - f.uncertainty_buffer
            risk = "projected_shortage" if balance < 0 else "reserve_at_risk" if headroom < 0 else "covered"
        out[f.ingredient_id] = {
            "name": inv.catalog.name_of(f.ingredient_id), "unit": f.unit,
            "available": available, "expected_usage": f.expected_usage,
            "uncertainty_buffer": f.uncertainty_buffer,
            "reserve_headroom": round(headroom, 3) if headroom is not None else None,
            "risk": risk or "unknown", "model": f.model, "method": f.method,
            "sample_count": f.sample_count, "warnings": list(f.warnings),
        }
    return out


def _forecast_lines(summary: Mapping[str, Mapping[str, Any]], items: Iterable[str],
                    requirements: Mapping[str, float]) -> list[str]:
    lines = []
    for item in items:
        s = summary.get(item)
        if s is None:
            continue
        unit = s["unit"]
        if s["expected_usage"] is None:
            lines.append(f"{s['name']}: no dinner forecast available.")
            continue
        after = s["reserve_headroom"] - requirements.get(item, 0)
        lines.append(f"{s['name']}: {s['available']:.0f} {unit} available, dinner forecast "
                     f"{s['expected_usage']:.0f} {unit} (+{s['uncertainty_buffer']:.0f} {unit} buffer); "
                     f"{after:+.0f} {unit} reserve headroom after this order.")
    return lines


def _score_line(routed, policy: RankingPolicy) -> str | None:
    f = routed.ranking_factors
    if routed.ranking_score is None or not f:
        return None
    return (f"Score {routed.ranking_score:.3f} = {policy.similarity_weight:.2f} x similarity {f['similarity']:.2f}"
            f" + {policy.reserve_weight:.2f} x forecast reserve {f['reserve']:.2f}"
            f" + {policy.cost_weight:.2f} x cost {f['cost']:.2f} (cutoff 0.75).")


async def route_options(inv, evaluation, *, now: datetime | None = None, history=None,
                        explainer=None) -> RoutingResult:
    """Route an inventory ``RequestEvaluation`` through the forecast engine. Read-only."""
    now = _check_now(now)
    records = _history_records(inv, history)
    if not evaluation.options:
        return await _route_as_asked(inv, evaluation, now, records, explainer)

    gateway = make_gateway(inv)
    by_index: dict[int, tuple[Any, Any]] = {}
    decisions = []
    plans: dict[int, bool] = {}
    for request, indexes in build_modification_requests(inv, evaluation, now=now, history=records):
        decision = await decide_modification(request, gateway)
        decisions.append((request, decision))
        for routed in decision.customer_options + decision.staff_options + decision.unavailable_options:
            by_index[int(routed.option_id.split("-", 1)[1])] = (routed, request)
        for i in indexes:
            plans[i] = all(c["with"] is None for c in evaluation.options[i].changes)

    first_request, first_decision = decisions[0]
    committed = first_request.evaluation.committed_in_horizon or {}
    summary = _forecast_summary(inv, first_decision.forecasts, committed)

    choices, unavailable = [], []
    for index in sorted(by_index):
        routed, request = by_index[index]
        option = evaluation.options[index]
        leave_off_only = plans[index]
        swapped_in = [_target_id(inv, c["with"]) for c in option.changes if c["with"]]
        reasons = list(routed.reasons)
        score_line = None if leave_off_only else _score_line(routed, request.evaluation.policy)
        if score_line:
            reasons.append(score_line)
        if leave_off_only:
            reasons.append("Leaves an item off; no replacement, so no similarity score applies.")
        reasons += _forecast_lines(summary, swapped_in, routed.requirements)
        if routed.route == "unavailable":
            unavailable.append({"option_index": index, "label": routed.label, "reasons": reasons})
            continue
        choices.append(RoutedChoice(
            option_index=index, route=routed.route, label=routed.label,
            score=None if leave_off_only else routed.ranking_score,
            similarity=option.similarity,
            price_change_cents=None if routed.price_change_cents is None else option.price_delta_cents,
            reasons=reasons, staff_notes=list(routed.staff_review_notes),
        ))

    def order(c: RoutedChoice):
        leave = c.score is None
        return (c.route != "customer", leave, -(c.score or 0.0), c.option_index)

    choices.sort(key=order)
    staff_notes = list(dict.fromkeys(n for _, d in decisions for n in d.staff_review_notes))
    if any(c.route == "customer" for c in choices):
        status = "reply_to_customer"
        message = f"Proposed: {choices[0].label}. Please review the change and price before accepting."
    elif choices:
        status = "needs_staff_review"
        message = (f"Proposed: {choices[0].label}. The kitchen is confirming your request before it is final."
                   if evaluation.requires_staff_review else
                   "Restaurant staff must review this request before an option can be offered.")
    else:
        status, message = "unavailable", NO_OPTION_MESSAGE
    return RoutingResult(
        status=status, customer_message=message, choices=choices, unavailable=unavailable,
        staff_review_notes=staff_notes, expires_at=min(d.expires_at for _, d in decisions),
        forecast_summary=summary, raw=[d.model_dump(mode="json") for _, d in decisions],
    )


async def _route_as_asked(inv, evaluation, now, records, explainer) -> RoutingResult:
    """No alternatives: either the request works as asked, or nothing can fix it."""
    feasible = evaluation.status in ("ready", "needs_staff_review") and evaluation.check.ok
    if not feasible:
        problems = [r.message for r in evaluation.reasons or evaluation.check.reasons if r.status != "info"]
        return RoutingResult(
            status="unavailable", customer_message=" ".join(problems) or NO_OPTION_MESSAGE, choices=[],
            unavailable=[], staff_review_notes=[], expires_at=now + timedelta(seconds=FRESHNESS_SECONDS),
            forecast_summary={}, raw=[],
        )
    exclude, diets = _flags(evaluation)
    restrictions = _restrictions(evaluation, exclude, diets)
    top_notes = _staff_notes(evaluation, restrictions)
    reqs = evaluation.check.requirements
    items = sorted(reqs)
    forecast = _forecast(inv, items, now, records)
    committed = _committed(inv, items)
    snapshot = await make_gateway(inv).snapshot(forecast.ingredients, as_of=now, valid_until=forecast.end,
                                                committed_in_horizon=committed)
    outlook = await forecast_inventory(OutlookRequest(
        evaluated_at=now, forecast=forecast, inventory=snapshot, max_snapshot_age_seconds=FRESHNESS_SECONDS,
        restrictions=restrictions, staff_review_notes=top_notes,
    ), explainer=explainer)
    summary = _forecast_summary(inv, outlook.forecasts, committed)

    reasons, notes, raw = [], [], [outlook.model_dump(mode="json")]
    for item in items:
        s = summary[item]
        if s["risk"] in ("reserve_at_risk", "projected_shortage"):
            reasons.append(f"Forecast: {s['name']} is {s['risk'].replace('_', ' ')} for this dinner "
                           f"({s['reserve_headroom']:+.0f} {s['unit']} reserve headroom before this order).")
    # Extras: the engine's extra-portion assessment for every item an "add" op increases.
    # Conservative: the whole order's quantity of that item is assessed, because this order is
    # not yet committed in the snapshot.
    ops = [op for op in evaluation.base_ops]
    without_adds = [op for op in ops if op.get("op") != "add"]
    if len(without_adds) != len(ops):
        base = inv.check(evaluation.dish, without_adds, evaluation.qty).requirements
        for item, qty in reqs.items():
            if qty > base.get(item, 0):
                assessment = assess_extra(outlook, item, float(qty))
                raw.append(assessment.model_dump(mode="json"))
                line = f"Extra {inv.catalog.name_of(item)}: {assessment.reason}"
                if assessment.decision == "supported":
                    reasons.append(line)
                elif assessment.decision != "needs_staff_review" or not evaluation.requires_staff_review:
                    notes.append(line)
    if evaluation.requires_staff_review:
        notes = list(outlook.staff_review_notes) + notes
    route = "staff" if evaluation.requires_staff_review or notes else "customer"
    choice = RoutedChoice(
        option_index=None, route=route, label="As requested", score=None, similarity=None,
        price_change_cents=evaluation.check.price_delta_cents, reasons=reasons,
        staff_notes=list(dict.fromkeys(notes)),
    )
    staff_notes = list(dict.fromkeys(list(outlook.staff_review_notes) + notes))
    if route == "customer":
        status, message = "reply_to_customer", "We can make this as requested. Please confirm your order."
    else:
        status = "needs_staff_review"
        message = ("The kitchen is confirming your request before it is final."
                   if evaluation.requires_staff_review else
                   "Restaurant staff must review this request before it is confirmed.")
    return RoutingResult(
        status=status, customer_message=message, choices=[choice], unavailable=[],
        staff_review_notes=staff_notes,
        expires_at=min(forecast.end, now + timedelta(seconds=FRESHNESS_SECONDS)),
        forecast_summary=summary, raw=raw,
    )


__all__ = ["DEMO_EVALUATION_TIME", "RoutedChoice", "RoutingResult", "InventoryFunctions", "SAFETY_REVIEW_NOTE",
           "build_modification_requests", "inventory_functions", "make_changes", "make_gateway", "route_options"]
