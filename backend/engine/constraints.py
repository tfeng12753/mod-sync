"""Hard restriction checks against the entire recipe, never LLM similarity."""
from .schemas import Candidate, EvaluationRequest

SAFETY_REVIEW_NOTE = (
    "An AI model cannot certify that a dish or ingredient is safe or allergen-free. "
    "Allergy and dietary restriction requests must go to restaurant staff for review before confirmation."
)


def restriction_checks(request: EvaluationRequest, candidate: Candidate):
    profiles = {p.ingredient_id: p for p in request.ingredient_profiles}
    reasons, unknown = [], False
    notes = list(dict.fromkeys(request.staff_review_notes + candidate.staff_review_notes +
                               [r.staff_review_note for r in request.restrictions] +
                               ([SAFETY_REVIEW_NOTE] if request.restrictions else [])))
    for requirement in candidate.requirements:
        for restriction in request.restrictions:
            profile = profiles.get(requirement.ingredient_id)
            name = f"{requirement.ingredient_id}: {restriction.kind} {restriction.value}"
            if profile is None or not profile.verified or profile.unit != requirement.unit:
                reasons.append(f"{name}: verified ingredient information unavailable.")
                unknown = True
                continue
            if restriction.kind == "allergy":
                contains = profile.allergen_status.get(restriction.value)
                meets = None if contains is None else not contains
            else:
                meets = profile.dietary_status.get(restriction.value)
            if meets is False:
                reasons.append(f"{name}: restriction violated; option blocked.")
            elif meets is None:
                reasons.append(f"{name}: restriction compliance unknown.")
                unknown = True
    # Even a verified ingredient pass requires restaurant staff to confirm the
    # preparation and cross-contact process before accepting a restricted order.
    return reasons, unknown, notes
