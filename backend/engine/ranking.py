"""Read-only ranking. No reservations, inventory mutations, or order confirmation."""
from .schemas import (
    DecisionFact, EvaluationRequest, EvaluationResult, ForecastResult,
    IngredientTradeoff, RankedOption,
)
from .constraints import restriction_checks


def rank_candidates(request: EvaluationRequest, forecasts: ForecastResult) -> EvaluationResult:
    inventory = {p.ingredient_id: p for p in request.inventory.positions}
    predictions = {p.ingredient_id: p for p in forecasts.forecasts}
    snapshot = request.inventory
    snapshot_issue = None
    age = (request.evaluated_at - snapshot.as_of).total_seconds()
    if age < 0 or age > request.max_snapshot_age_seconds:
        snapshot_issue = "Inventory snapshot is stale or dated in the future."
    if snapshot.valid_until < forecasts.end:
        snapshot_issue = "Inventory snapshot does not cover the forecast horizon."
    ranked, excluded, review_options = [], [], []
    for candidate in request.candidates:
        reasons, tradeoffs, facts = [], [], []
        restriction_reasons, restriction_unknown, notes = restriction_checks(request, candidate)
        reasons.extend(restriction_reasons)
        restriction_violated = any("restriction violated" in r for r in restriction_reasons)
        review = candidate.requires_staff_review or bool(notes) or restriction_unknown
        if candidate.requires_staff_review and not notes:
            notes = ["Upstream service requires staff review."]
        if not candidate.approved:
            reasons.append("Substitution is not restaurant-approved.")
        for label, value in [("Customer constraints", candidate.customer_constraints_met),
                             ("Preparation feasibility", candidate.preparation_feasible)]:
            if value is not True:
                reasons.append(f"{label}: {'not met' if value is False else 'unknown'}.")
                review = review or value is None
        if candidate.inventory_check_passed is not True:
            reasons.extend(candidate.inventory_reasons or ["Inventory check did not pass."])
            review = review or candidate.inventory_check_passed is None
        if candidate.similarity is None:
            reasons.append("Culinary similarity unavailable; staff review required.")
            review = True
        if snapshot_issue:
            reasons.append(snapshot_issue)
            review = True
        for requirement in candidate.requirements:
            ingredient_id = requirement.ingredient_id
            stock = inventory.get(ingredient_id)
            forecast = predictions.get(ingredient_id)
            if stock is None or stock.unit != requirement.unit:
                reasons.append(f"{ingredient_id}: inventory missing or units disagree.")
                review = True
                continue
            # committed_in_horizon is included in gross forecast demand. Subtract it
            # from demand once, while reserving it from physical stock once.
            available = max(0.0, stock.available_now - stock.committed_in_horizon)
            if requirement.quantity > available:
                reasons.append(f"{ingredient_id}: insufficient uncommitted stock.")
            if forecast is None or forecast.unit != requirement.unit or forecast.expected_usage is None:
                reasons.append(f"{ingredient_id}: forecast unavailable or units disagree.")
                review = True
                tradeoffs.append(IngredientTradeoff(
                    **requirement.model_dump(exclude={"quantity"}),
                    requested_quantity=requirement.quantity,
                    available_after_commitments=available, uncommitted_expected_demand=None,
                    uncertainty_buffer=None, projected_surplus=None, reserve_score=None,
                ))
                continue
            remaining = max(0.0, forecast.expected_usage - stock.committed_in_horizon)
            reserve = remaining + forecast.uncertainty_buffer
            surplus = available - requirement.quantity - reserve
            # Positive surplus is headroom, not proven avoidable waste.
            scale = max(reserve, requirement.quantity)
            reserve_score = (1 + max(-1, min(1, surplus / scale))) / 2
            tradeoffs.append(IngredientTradeoff(
                **requirement.model_dump(exclude={"quantity"}),
                requested_quantity=requirement.quantity,
                available_after_commitments=round(available, 3),
                uncommitted_expected_demand=round(remaining, 3),
                uncertainty_buffer=forecast.uncertainty_buffer,
                projected_surplus=round(surplus, 3), reserve_score=reserve_score,
            ))
            facts.append(DecisionFact(
                fact_id=f"reserve:{ingredient_id}",
                text=(f"{ingredient_id}: reserve headroom after this recipe, commitments, "
                      f"expected remaining demand and buffer is {surplus:.1f} {requirement.unit} "
                      "(negative means a reserve deficit)."),
            ))
        can_score = not reasons
        eligible = can_score and not review
        factors, score = {}, None
        if can_score:
            policy = request.policy
            reserve_factor = min(t.reserve_score for t in tradeoffs)
            cost = candidate.ingredient_cost_change_cents + candidate.additional_labor_cents
            cost_factor = max(0, min(1, 0.5 - cost / (2 * policy.cost_scale_cents)))
            factors = {"similarity": candidate.similarity,
                       "reserve": reserve_factor, "cost": cost_factor}
            total = policy.similarity_weight + policy.reserve_weight + policy.cost_weight
            score = round((policy.similarity_weight * candidate.similarity +
                           policy.reserve_weight * reserve_factor +
                           policy.cost_weight * cost_factor) / total, 6)
        facts.extend([
            DecisionFact(fact_id="cost", text=(
                f"Ingredient cost changes by {candidate.ingredient_cost_change_cents} cents; "
                f"additional labor is {candidate.additional_labor_cents} cents."
            )),
            DecisionFact(fact_id="price", text=f"Customer price changes by {candidate.price_change_cents} cents."),
        ])
        option = RankedOption(
            option_id=candidate.option_id, label=candidate.label, score=score,
            eligible=eligible, requires_staff_review=review, reasons=reasons + notes,
            tradeoffs=tradeoffs, ingredient_cost_change_cents=candidate.ingredient_cost_change_cents,
            additional_labor_cents=candidate.additional_labor_cents,
            price_change_cents=candidate.price_change_cents, factors=factors, facts=facts,
            explanation=(" ".join(f.text for f in facts) + " " + " ".join(notes)).strip()
                        if can_score else " ".join(reasons + notes),
            staff_review_notes=notes, similarity=candidate.similarity,
            similarity_source=candidate.similarity_source if candidate.similarity is not None else "unavailable",
            similarity_rationale=candidate.similarity_rationale,
            blocked_by_restriction=restriction_violated,
        )
        (ranked if eligible else review_options if can_score else excluded).append(option)
    ranked.sort(key=lambda option: (-option.score, option.option_id))
    review_options.sort(key=lambda option: (-option.score, option.option_id))
    return EvaluationResult(
        inventory_version=snapshot.version,
        status="needs_customer_approval" if ranked else (
            "needs_staff_review" if review_options or any(
                o.requires_staff_review and not o.blocked_by_restriction for o in excluded
            ) else "unavailable"
        ),
        recommended_option_id=ranked[0].option_id if ranked else None,
        forecasts=forecasts, ranked_options=ranked, excluded_options=excluded,
        review_options=review_options,
        staff_review_notes=list(dict.fromkeys(request.staff_review_notes +
            [r.staff_review_note for r in request.restrictions] +
            [note for o in ranked + review_options + excluded for note in o.staff_review_notes])),
        warnings=["Reserve deficits are ranking penalties, not hard stock limits. "
                  "Confirmation must recheck inventory and reserve stock in the inventory service."] +
                 (["Inventory exposes net availability without commitment totals; gross forecast "
                   "demand is conservatively reserved, which may overstate shortages."]
                  if not snapshot.commitments_known else []) +
                 (["Inventory has no authoritative snapshot version; confirmation must call check again."]
                  if not snapshot.version_is_authoritative else []),
    )
