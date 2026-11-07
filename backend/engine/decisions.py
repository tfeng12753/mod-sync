"""Route modifications using inventory similarity, catalog facts, and menu prices.

Catalog/pricing data is trusted backend data, never model output or customer input.
No below-threshold proposal is sent to staff as a way around the cutoff.
"""
from __future__ import annotations

from typing import Literal
from math import isclose
from datetime import datetime, timedelta
from decimal import Decimal
from pydantic import Field, model_validator

from .adapters import InventoryGateway
from .integration import ConnectedEvaluationRequest, evaluate_with_inventory
from .schemas import Contract, ForecastResult, Unit


class CatalogItem(Contract):
    item_id: str = Field(min_length=1)
    in_inventory: bool = False
    on_menu: bool = False
    # Full flattened composition, supplied/verified by inventory/recipes.
    contains_ingredients: list[str] | None = None
    composition_verified: bool = False
    # Can this item itself be portioned in its current preparation state?
    # Guacamole can be portionable while raw avocado inside it is not extractable.
    can_serve_separately: bool | None = None


class ModificationOffer(Contract):
    option_id: str
    offered_item_id: str | None = None  # None for simple omission
    approved_for_dish: bool
    portion_quantity: float | None = Field(default=None, gt=0)
    portion_unit: Unit | None = None
    # Inventory-approved ingredients consumed by one extra/replacement portion.
    portion_requirements: dict[str, float] = Field(default_factory=dict)
    # Optional explicit quantities removed per dish by an approved swap.
    # Without this map, remove the whole target-containing component(s).
    removed_requirements: dict[str, float] | None = None
    # Menu-approved price delta for one extra/replacement portion. Zero is explicit.
    menu_price_change_cents: int | None = None
    menu_price_reference: str | None = None

    @model_validator(mode="after")
    def valid_portion(self):
        if any(q <= 0 for q in list(self.portion_requirements.values()) +
               list((self.removed_requirements or {}).values())):
            raise ValueError("portion requirements must be positive")
        if self.menu_price_change_cents is not None and not (self.menu_price_reference or "").strip():
            raise ValueError("menu price requires an authoritative menu/rule reference")
        return self


class DecisionPolicy(Contract):
    minimum_score: float = Field(default=0.75, ge=0, le=1)
    threshold_basis: Literal["combined", "culinary"] = "combined"
    # Optional restaurant-authorized rule when no menu modifier price exists.
    # Nothing is waived/charged automatically unless this entire rule is configured.
    pricing_policy_id: str | None = None
    absorb_incremental_cost_up_to_cents: int | None = Field(default=None, ge=0)
    approved_upcharge_cents: int | None = Field(default=None, ge=0)
    omission_price_change_cents: int = 0


class ModificationRequest(Contract):
    action: Literal["substitute", "omit", "extra"]
    dish_id: str
    target_ingredient_id: str
    # Substitution for avoidance excludes the target, including inside mixtures.
    # Availability-only replacement may explicitly allow it in a prepared component.
    exclude_target: bool = True
    extra_portions_per_dish: int = Field(default=1, ge=1)
    original_changes: dict = Field(default_factory=dict)
    evaluation: ConnectedEvaluationRequest
    catalog: list[CatalogItem]
    offers: list[ModificationOffer]
    policy: DecisionPolicy = Field(default_factory=DecisionPolicy)

    @model_validator(mode="after")
    def unique_ids(self):
        for values in [[i.item_id for i in self.catalog], [o.option_id for o in self.offers]]:
            if len(values) != len(set(values)):
                raise ValueError("duplicate catalog or offer ID")
        if {o.option_id for o in self.offers} != {c.option_id for c in self.evaluation.candidates}:
            raise ValueError("each candidate must have exactly one catalog offer")
        if any(c.dish_id != self.dish_id for c in self.evaluation.candidates):
            raise ValueError("candidate dish IDs must match the requested dish")
        return self


class RoutedOption(Contract):
    option_id: str
    label: str
    route: Literal["customer", "staff", "unavailable"]
    offered_item_id: str | None
    portion_quantity: float | None
    portion_unit: Unit | None
    dish_quantity: int
    portions_per_dish: int
    total_portions: int
    total_offered_quantity: float | None
    requirements: dict[str, float]
    removed_requirements: dict[str, float]
    similarity: float | None
    similarity_required: bool
    ranking_score: float | None
    ranking_factors: dict[str, float]
    price_change_cents: int | None
    pricing_reference: str | None
    requires_customer_acceptance: bool
    reasons: list[str]
    staff_review_notes: list[str]


class ModificationDecision(Contract):
    action: str
    status: Literal["reply_to_customer", "needs_staff_review", "unavailable"]
    minimum_score: float
    threshold_basis: str
    customer_message: str
    customer_options: list[RoutedOption]
    staff_options: list[RoutedOption]
    unavailable_options: list[RoutedOption]
    staff_review_notes: list[str]
    forecasts: ForecastResult
    inventory_version: str
    warnings: list[str]
    evaluated_at: datetime
    expires_at: datetime
    requires_inventory_recheck_on_confirmation: Literal[True] = True


def decision_is_current(decision: ModificationDecision, now: datetime) -> bool:
    """A freshness check, never permission to skip inventory/staff confirmation."""
    if now.utcoffset() is None:
        raise ValueError("now must include a UTC offset")
    return decision.evaluated_at <= now < decision.expires_at


def _same_quantities(left, right):
    return set(left) == set(right) and all(
        isclose(q, right[i], abs_tol=1e-6, rel_tol=1e-9) for i, q in left.items()
    )


def _combined_score(option, policy):
    if option.score is None:
        return None
    # Compare the calculation, not the rounded diagnostic score. Decimal also
    # makes the inclusive 0.75 boundary stable for decimal policy inputs.
    weights = {"similarity": policy.similarity_weight, "reserve": policy.reserve_weight,
               "cost": policy.cost_weight}
    numerator = sum(Decimal(str(w)) * Decimal(str(option.factors[k])) for k, w in weights.items())
    return numerator / sum(Decimal(str(w)) for w in weights.values())


def _price(request, offer, candidate):
    multiplier = request.evaluation.qty * (
        request.extra_portions_per_dish if request.action == "extra" else 1
    )
    if offer.menu_price_change_cents is not None:
        return offer.menu_price_change_cents * multiplier, offer.menu_price_reference
    if request.action == "omit" and offer.offered_item_id is None:
        return request.policy.omission_price_change_cents * request.evaluation.qty, "omission-policy"
    policy = request.policy
    if policy.pricing_policy_id and policy.absorb_incremental_cost_up_to_cents is not None:
        # Candidate cost/labor deltas already describe the WHOLE order, not one portion.
        cost = candidate.ingredient_cost_change_cents + candidate.additional_labor_cents
        if cost <= policy.absorb_incremental_cost_up_to_cents:
            return 0, policy.pricing_policy_id
        if (policy.approved_upcharge_cents is not None and
                policy.approved_upcharge_cents >= cost - policy.absorb_incremental_cost_up_to_cents):
            return policy.approved_upcharge_cents, policy.pricing_policy_id
    return None, None


async def decide_modification(request: ModificationRequest, gateway: InventoryGateway) -> ModificationDecision:
    """Read-only decision; staff/customer acceptance and confirm remain API-owned."""
    catalog = {i.item_id: i for i in request.catalog}
    offers = {o.option_id: o for o in request.offers}
    original = await gateway.requirements(request.dish_id, request.original_changes)
    baseline = {i: q * request.evaluation.qty for i, q in original.items()}
    evaluation_request = request.evaluation.model_copy(deep=True)
    quoted, preliminary = {}, {}
    for candidate in evaluation_request.candidates:
        offer = offers[candidate.option_id]
        exact = request.action == "omit" and offer.offered_item_id is None or (
            request.action == "extra" and offer.offered_item_id == request.target_ingredient_id
        )
        if exact:
            # Identity/direct omission has no replacement similarity question.
            candidate.similarity = 1.0
            candidate.similarity_source = "not_applicable"
        else:
            # Inventory owns the OpenRouter call and the culinary score.
            if candidate.similarity_source != "inventory":
                candidate.similarity = None
        price, reference = _price(request, offer, candidate)
        quoted[candidate.option_id] = (price, reference)
        candidate.price_change_cents = price if price is not None else 0  # never quoted as free if unknown
        preliminary[candidate.option_id] = (exact, candidate.similarity)
    result = await evaluate_with_inventory(evaluation_request, gateway)
    computed = {o.option_id: o for o in result.ranked_options + result.review_options + result.excluded_options}
    original_candidates = {c.option_id: c for c in request.evaluation.candidates}
    units = {i.ingredient_id: i.unit for i in request.evaluation.forecast.ingredients}
    customer, staff, unavailable = [], [], []
    for option_id, option in computed.items():
        offer = offers[option_id]
        candidate = original_candidates[option_id]
        exact, similarity = preliminary[option_id]
        reasons, notes = [], list(option.staff_review_notes)
        blocked, uncertain = False, False
        combined_score = _combined_score(option, request.evaluation.policy)
        threshold_value = (Decimal(str(similarity)) if similarity is not None else None
                           ) if request.policy.threshold_basis == "culinary" else combined_score
        if not exact and (similarity is None or threshold_value is None or
                          threshold_value < Decimal(str(request.policy.minimum_score))):
            blocked = True
            reasons.append(f"We cannot accommodate this option: its {request.policy.threshold_basis} "
                           f"score is missing or below {request.policy.minimum_score:.2f}.")
        if not offer.approved_for_dish:
            blocked = True
            reasons.append("This option is not an approved modification for the dish.")
        if request.action in {"extra", "substitute"} and offer.offered_item_id is None:
            blocked = True
            reasons.append("No registered replacement or extra item was supplied.")
        # Every ingredient in the final recipe must be registered, including mixtures.
        for requirement in option.tradeoffs:
            item = catalog.get(requirement.ingredient_id)
            if item is None or not (item.in_inventory or item.on_menu):
                blocked = True
                reasons.append(f"{requirement.ingredient_id}: not registered in inventory or on the menu.")
        if offer.offered_item_id is not None:
            item = catalog.get(offer.offered_item_id)
            if item is None or not (item.in_inventory or item.on_menu):
                blocked = True
                reasons.append("The offered item is not in inventory or on the menu.")
            elif item.can_serve_separately is False:
                blocked = True
                reasons.append("The offered item cannot be separated or portioned in its current state.")
            elif item.can_serve_separately is None:
                uncertain = True
                notes.append("Staff must verify that the offered item can be portioned separately.")
            if not offer.portion_requirements or offer.portion_quantity is None or offer.portion_unit is None:
                uncertain = True
                notes.append("Inventory must supply an approved portion and its ingredient requirements.")
            if offer.offered_item_id in units and offer.portion_unit is not None and offer.portion_unit != units[offer.offered_item_id]:
                blocked = True
                reasons.append("Offered portion units do not match the inventory unit; an explicit conversion is required.")
            if offer.offered_item_id in offer.portion_requirements and offer.portion_quantity is not None and not isclose(
                offer.portion_requirements[offer.offered_item_id], offer.portion_quantity,
                abs_tol=1e-6, rel_tol=1e-9,
            ):
                blocked = True
                reasons.append("The displayed portion quantity does not match the prepared stock consumed.")
            if item and offer.offered_item_id not in offer.portion_requirements:
                # A menu component may be represented by its raw recipe rather
                # than stocked as a prepared item. Verify the declared portion
                # actually supplies its composition; never label beans avocado.
                actual_composition = set(offer.portion_requirements)
                portion_known = True
                for ingredient_id in offer.portion_requirements:
                    component = catalog.get(ingredient_id)
                    if component and component.composition_verified and component.contains_ingredients is not None:
                        actual_composition.update(component.contains_ingredients)
                    else:
                        portion_known = False
                if not item.composition_verified or item.contains_ingredients is None or not portion_known:
                    uncertain = True
                    notes.append("Staff must verify the portion recipe matches the offered item.")
                elif not set(item.contains_ingredients or [item.item_id]) <= actual_composition:
                    blocked = True
                    reasons.append("The portion recipe does not supply the item being offered.")
        # Inventory check is authoritative. Re-read the final recipe to examine
        # composition and added portions; no inventory mutation occurs here.
        final = await gateway.requirements(request.dish_id, candidate.changes)
        final = {i: q * request.evaluation.qty for i, q in final.items()}
        for ingredient_id in final:
            item = catalog.get(ingredient_id)
            if item is None or not (item.in_inventory or item.on_menu):
                blocked = True
                reasons.append(f"{ingredient_id}: not registered in inventory or on the menu.")
        checked = {t.ingredient_id: t.requested_quantity for t in option.tradeoffs}
        if not _same_quantities(final, checked):
            blocked = True
            reasons.append("The recipe changed after its checks; reevaluate the entire request before proposing it.")
        if request.action == "omit" or (request.action == "substitute" and request.exclude_target):
            for ingredient_id in final:
                item = catalog.get(ingredient_id)
                if ingredient_id == request.target_ingredient_id or (
                    item and item.composition_verified and item.contains_ingredients is not None
                    and request.target_ingredient_id in item.contains_ingredients
                ):
                    blocked = True
                    reasons.append("The final recipe still contains the ingredient requested for removal, "
                                   "including inside a prepared component.")
                elif item is None or not item.composition_verified or item.contains_ingredients is None:
                    uncertain = True
                    notes.append(f"Staff must verify {ingredient_id}'s complete composition.")
        removed = {}
        if request.action == "extra":
            item = catalog.get(offer.offered_item_id)
            if item and offer.offered_item_id != request.target_ingredient_id:
                if not item.composition_verified or item.contains_ingredients is None:
                    uncertain = True
                    notes.append("Staff must verify the composite extra contains the requested ingredient.")
                elif request.target_ingredient_id not in item.contains_ingredients:
                    blocked = True
                    reasons.append("The composite extra does not contain the requested ingredient.")
            multiplier = request.evaluation.qty * request.extra_portions_per_dish
            expected = dict(baseline)
            for ingredient_id, q in offer.portion_requirements.items():
                expected[ingredient_id] = expected.get(ingredient_id, 0) + q * multiplier
            if not _same_quantities(expected, final):
                blocked = True
                reasons.append("The inventory recipe does not match the base order plus the approved extra portion.")
        else:
            def contains_target(ingredient_id):
                if ingredient_id == request.target_ingredient_id:
                    return True
                item = catalog.get(ingredient_id)
                if item and item.composition_verified and item.contains_ingredients is not None:
                    return request.target_ingredient_id in item.contains_ingredients
                return None
            removed = ({i: q * request.evaluation.qty for i, q in offer.removed_requirements.items()}
                       if offer.removed_requirements is not None else
                       {i: q for i, q in baseline.items() if contains_target(i) is True})
            for ingredient_id, q in removed.items():
                if ingredient_id not in baseline or q > baseline.get(ingredient_id, 0):
                    blocked = True
                    reasons.append("The proposed removal exceeds the original recipe quantity.")
                if contains_target(ingredient_id) is False:
                    blocked = True
                    reasons.append("The modification would remove an unrelated ingredient.")
                elif contains_target(ingredient_id) is None:
                    uncertain = True
                    notes.append("Staff must verify the removed component contains the targeted ingredient.")
            if request.action == "substitute" and not removed:
                blocked = True
                reasons.append("The substitution does not replace a target ingredient in the original recipe.")
            expected = {i: q - removed.get(i, 0) for i, q in baseline.items()}
            expected = {i: q for i, q in expected.items() if q > 0}
            if offer.offered_item_id is not None:
                for ingredient_id, q in offer.portion_requirements.items():
                    expected[ingredient_id] = expected.get(ingredient_id, 0) + q * request.evaluation.qty
            if not _same_quantities(expected, final):
                blocked = True
                reasons.append("The customized recipe does not match the approved removal and replacement; "
                               "unrelated ingredients must remain unchanged.")
        price, reference = quoted[option_id]
        if price is None:
            uncertain = True
            notes.append("Staff must set a menu-approved price; no automatic charge or free extra is assumed.")
        if option.blocked_by_restriction:
            blocked = True
        if option.score is None:
            # An unresolved/hard-failed inventory or restriction check cannot
            # become a feasible staff proposal simply because it carries a note.
            blocked = True
            reasons.extend(option.reasons)
        if any(t.projected_surplus is not None and t.projected_surplus < 0 for t in option.tradeoffs):
            uncertain = True
            notes.append("This option consumes forecast reserves; staff must review the shortage exposure.")
        route = "unavailable" if blocked else "staff" if uncertain or notes else "customer"
        routed = RoutedOption(
            option_id=option_id, label=option.label, route=route, offered_item_id=offer.offered_item_id,
            portion_quantity=offer.portion_quantity, portion_unit=offer.portion_unit,
            dish_quantity=request.evaluation.qty,
            portions_per_dish=(request.extra_portions_per_dish if request.action == "extra" else 1)
                             if offer.offered_item_id is not None else 0,
            total_portions=(request.evaluation.qty * (request.extra_portions_per_dish
                            if request.action == "extra" else 1)) if offer.offered_item_id is not None else 0,
            total_offered_quantity=(offer.portion_quantity * request.evaluation.qty *
                (request.extra_portions_per_dish if request.action == "extra" else 1))
                if offer.offered_item_id is not None and offer.portion_quantity is not None else None,
            requirements=checked, removed_requirements=removed,
            similarity=similarity if not exact else None, similarity_required=not exact,
            ranking_score=float(combined_score) if combined_score is not None else None,
            ranking_factors=option.factors,
            price_change_cents=price, pricing_reference=reference,
            requires_customer_acceptance=route != "unavailable", reasons=list(dict.fromkeys(reasons)),
            staff_review_notes=list(dict.fromkeys(notes)),
        )
        (customer if route == "customer" else staff if route == "staff" else unavailable).append(routed)
    def key(option):
        score = option.ranking_score
        return (-(score if score is not None else -1), option.option_id)
    customer.sort(key=key)
    staff.sort(key=key)
    status = "reply_to_customer" if customer else "needs_staff_review" if staff else "unavailable"
    message = ("Please review the available option, portion and price change before accepting."
               if customer else "Restaurant staff must review this request before an option can be offered."
               if staff else "We cannot accommodate this request with the available menu and inventory options.")
    return ModificationDecision(
        action=request.action, status=status, minimum_score=request.policy.minimum_score,
        threshold_basis=request.policy.threshold_basis,
        customer_message=message, customer_options=customer, staff_options=staff,
        unavailable_options=unavailable, staff_review_notes=result.staff_review_notes,
        forecasts=result.forecasts, inventory_version=result.inventory_version, warnings=result.warnings,
        evaluated_at=request.evaluation.evaluated_at,
        expires_at=min(request.evaluation.usable_until, request.evaluation.evaluated_at +
                       timedelta(seconds=request.evaluation.max_snapshot_age_seconds)),
    )
