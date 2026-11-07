# Team integration contract — version 1.0

The authoritative Python types are in `backend/engine/schemas.py`. Generated JSON Schemas in this directory allow other backend sections and the frontend to validate fixtures without importing engine internals. All unknown fields are rejected.

## Ownership

| Owner | Supplies or handles |
| --- | --- |
| Forecasting/decision engine | Usage predictions, uncertainty reserve, weighted ranking, cutoff, customer/staff routing and approved-price calculations |
| Inventory teammate | Physical stock, prepared-component feasibility/composition, approved portion requirements, OpenRouter culinary similarity, checks, reservations and deductions |
| Menu/catalog owner | Registered items and dish-approved modifications, portions, menu modifier prices and authorized upcharge rules |
| Interpretation teammate (person two) | Structured modifications/restrictions, original staff-review notes, approved candidate references, customer and preparation context |
| API/data teammate | History, orchestration, validation errors, evaluation persistence, acceptance, version checks and atomic confirmation |

## Safety review statement

**An AI model cannot certify that a dish or ingredient is safe or allergen-free. Allergy and dietary restriction requests must go to restaurant staff for review before confirmation.**

Pass person two's `restrictions` and `staff_review_notes` through evaluation and outlook requests. A restriction has `kind` (`allergy` or `dietary`), canonical `value`, and `staff_review_note`. A missing note receives a default review note. All restricted evaluations also attach the statement above. Staff review is required even when ingredient checks pass.

`ingredient_profiles` must originate from verified restaurant/catalog data. For each ingredient, provide `verified`, `allergen_status`, and `dietary_status`. In `allergen_status`, true means present and false means verified absent; missing/null means unknown. In `dietary_status`, true means compliant and false means incompatible; missing/null means unknown. Identifiers must match person two's canonical restriction identifiers exactly. For example, an allergy to `soy` cannot be satisfied by another component with `allergen_status.soy: true`. An unverified or mismatched ingredient profile cannot establish compatibility.

The entire customized recipe, including premixed ingredients represented in inventory requirements, is checked. Known violations are blocked with `blocked_by_restriction: true`. Unknown compliance requires review. Passing composition checks does not certify preparation or cross-contact safety. The model scores culinary fit only and cannot clear these flags or change notes.

`review_options` contains scored proposals that passed known hard checks but still need staff review; these are not eligible customer recommendations. Preserve per-option and top-level notes in the frontend and staff ticket. The API must enforce staff approval before inventory confirmation. No review flag authorizes accepting a known violation.

## Actual inventory function adapter

`InventoryGateway` wraps `requirements(dish_id, changes)`, `check(dish_id, changes, qty=1)`, `available(ingredient_id)`, and `confirm(order)`, supporting sync and async implementations. `evaluate_with_inventory(ConnectedEvaluationRequest, gateway, ...)` assembles the lower-level engine request. Each candidate needs `dish_id` and `changes`; its complete requirements are populated from inventory. One-dish requirements multiplied by `qty` must agree with `check`'s full order quantities.

Configure the team's actual success/blocked `Result.status` strings in the gateway. Unknown statuses require staff review. The adapter accepts a mapping or attribute-based Result with a string `status`, list of string `reasons`, and a quantity-map `requirements`. Changes pass through unchanged; their exact schema is owned by inventory/API.

`available()` already excludes amounts set aside. It is used without a second reservation deduction. With optional `committed_in_horizon` totals, the adapter adds them back only to normalize to the legacy gross snapshot fields below; the engine's single deduction then restores exactly the reported net availability. Without those totals, it uses net stock and gross forecast demand conservatively and returns a warning. It never infers commitment totals from the net number.

Supply historical usage and `usable_until` (a restaurant-confirmed horizon boundary) separately. These four functions do not expose them. Missing inventory versions are explicitly marked non-authoritative, and independent `available` calls are not an atomic snapshot. Recommendation never calls `confirm`; the API owner must enforce staff/customer acceptance, then call inventory `confirm(order)` for its own recheck and exactly-once mutation. The gateway does not retry mutations.

## Primary upstream API

`forecast_inventory(OutlookRequest, explainer=None)` takes `evaluated_at`, `forecast`, and `inventory`. No candidates or similarity scores are required. The standalone `forecast_usage` accepts history without inventory. `forecast.model` defaults to `autoets`; `historical` forces the prior baseline. AutoETS is selected only if it beats the baseline on three rolling validation periods, with the selected model, comparison MAEs, and limitations returned per ingredient.

The result includes the inventory version, ingredient forecasts, projected balances, reserve headroom, risk classifications, and extra capacity. Inventory changes affect these coverage fields, not the underlying demand prediction. No replacement ingredients are returned by this primary API.

`assess_extra(outlook, ingredient_id, quantity)` assesses an additional portion in the ingredient's canonical unit. The base recipe must already be accounted for in stock/commitments. The result reports physical availability, reserve preservation, number of identical extra portions within reserve capacity, and `supported`, `reserve_at_risk`, `unavailable`, or `needs_staff_review`. It does not establish portion approval, nutrition, price, or culinary suitability. Shared extra capacity must be reserved atomically downstream and recomputed after acceptance. Use a fresh outlook for new requests; never reuse a stale cached snapshot as live availability.

`fixtures/outlook_request.json` and `fixtures/outlook_response.json` illustrate the primary integration. `outlook_*.schema.json` and `extra_assessment.schema.json` are generated alongside the older evaluation schemas.

The older `evaluate_substitutions` API remains separate for the substitution team. The candidate/result sections below describe that downstream interface.

## Primary modification routing API

Call `decide_modification(ModificationRequest, InventoryGateway)` to enforce the complete business policy. The request contains `action` (`substitute`, `omit`, or `extra`), `dish_id`, target ingredient, a connected evaluation, trusted catalog items, and per-candidate offers. The server supplies catalog, scores, policy and pricing; the frontend cannot assert their authority.

Inventory supplies `similarity` and `similarity_source: "inventory"`. Inventory makes the OpenRouter similarity call. This engine does not duplicate it. Local OpenRouter support is limited to optional grounded explanations.

The default and agreed cutoff is **combined score >= 0.75**, using 55% similarity, 35% forecast reserve/headroom, and 10% cost. Forecast surplus can help a less similar proposal qualify. Both customer and staff proposal lists require the same cutoff; below it, return cannot accommodate. Direct omission and exact-item extra have no replacement-similarity requirement.

Use the calculation before display rounding for the cutoff; 0.7499999 is below threshold even if a frontend rounds it to 0.75. `ranking_score` on routed options reports the unrounded combined calculation.

`ModificationDecision` has `customer_options`, `staff_options`, and `unavailable_options`, with one request status: `reply_to_customer`, `needs_staff_review`, or `unavailable`. Each proposal identifies the real offered item, portion, combined score, factors, price delta, pricing reference, review notes, and customer-acceptance requirement. The result's customer message for no qualifying option is “We cannot accommodate this request with the available menu and inventory options.” Staff notes never authorize a known hard failure or bypass the cutoff.

For qualifying feasible options, person two's notes, allergy/dietary restrictions, forecast reserve exposure or unresolved price/composition/preparation facts require staff review. Only fully checked/priced options without those flags go directly to customer consideration. Known stock failures and incompatible ingredients never become staff proposals. Customer acceptance and required staff approval precede inventory confirmation.

Catalog items must be registered `in_inventory` or `on_menu`. `can_serve_separately` describes current preparation state. `composition_verified` and flattened `contains_ingredients` support checks inside prepared mixtures. For omissions/avoidance substitutions, the complete final recipe must exclude the target. A premixed component retaining the target fails. Replacing/removing the whole component may work when explicitly approved and disclosed. `exclude_target: false` allows availability-only substitutions containing the target but cannot override allergy/dietary restrictions.

Extra portions use authoritative `portion_requirements`; the entire modified recipe must equal the original recipe plus those quantities. The desired ingredient may be inside a composite extra such as guacamole, provided inventory verifies its presence, the composite is portionable, and the combined score qualifies. Raw avocado cannot be extracted from inseparable guacamole. Show actual composite quantity, not an asserted equal amount of pure avocado.

Omissions and substitutions must equal the original recipe minus the target-containing components plus the approved replacement requirements. Optional `removed_requirements` on an offer describes explicit per-dish removals; absent it, the engine removes the complete target-containing component(s). Unrelated ingredients must remain unchanged, and removal cannot exceed original quantities. A recipe change between its inventory check and the final read invalidates that proposal entirely; rerun evaluation before routing it to customer or staff.

Routed options include `dish_quantity`, `portions_per_dish`, `total_portions`, `total_offered_quantity`, and whole-order `requirements`/`removed_requirements`. Menu prices remain per portion on offers and whole-order price deltas on responses. For stocked prepared items, displayed quantity and unit must match consumed stock; no implicit unit conversions are allowed. These whole-order requirements are not an instruction to deduct stock before confirmation.

Decisions include server-supplied `evaluated_at` and an exclusive `expires_at` bound (default five minutes, capped by `usable_until`), plus `requires_inventory_recheck_on_confirmation: true`. The API must use its own clock and `decision_is_current(decision, now)` before showing/accepting cached offers. Expired decisions require reevaluation. Freshness does not reserve inventory or lock a menu price; acceptance still requires current pricing, required staff/customer approval and inventory's own confirm/recheck/exactly-once behavior. The adapter cannot provide an atomic snapshot or transaction guarantee not exposed by inventory.

Use `menu_price_change_cents` and `menu_price_reference` from the menu owner; multiply per-portion deltas by dish quantity and extra-portion count once. No automatic charge is inferred from an AI score or a candidate's guessed price. A restaurant-authorized pricing rule may absorb small whole-order ingredient/labor cost changes or apply a fixed approved upcharge covering the excess; absent a price/rule, a qualifying proposal requires staff pricing and has null price. Simple omission defaults to zero price change unless menu policy specifies otherwise. No real restaurant prices or absorption allowances have been provided.

`fixtures/modification_request.json` and `modification_response.json` are the complete synthetic example; JSON Schemas are supplied in this directory. Lower-level evaluation APIs do not replace this routing policy.

## Shared conventions

- Stable ingredient and option IDs; no matching by display names.
- Quantities use the ingredient's canonical `g`, `ml`, or `each` unit. Convert quantities upstream; the engine never guesses conversions. Quantities are finite numbers. Costs and prices are integer cents; signed changes are relative to the standard recipe.
- All timestamps include a UTC offset. `time_zone` is an IANA restaurant timezone, used for local weekday matching. Windows are `[start, end)` and elapsed durations must match historical periods. Daily totals cannot be used as dinner history.
- Forecast start equals `evaluated_at` in v1. Use a consistent evaluation timestamp across services. Snapshot age defaults to at most 300 seconds. The synthetic demo supplies its own historical evaluation time.
- History contains one ingredient total per complete period and source; duplicates and overlapping periods for the same ingredient/source are rejected. Record actual zeroes explicitly. Flag stockouts and incomplete periods. Caller selects `measured` or `recipe_estimated`; the engine never silently mixes them.

## Inventory snapshot

```json
{
  "version": "inventory-version-123",
  "as_of": "2026-11-07T17:00:00-05:00",
  "valid_until": "2026-11-07T20:00:00-05:00",
  "positions": [
    {
      "ingredient_id": "tofu",
      "unit": "g",
      "available_now": 1700,
      "committed_in_horizon": 500
    }
  ]
}
```

This is the engine's normalized snapshot representation. `available_now` is usable stock **before deducting `committed_in_horizon`**. It excludes unusable quantities and outside-horizon allocations. Stock must remain usable through the whole horizon. `committed_in_horizon` is accepted demand inside the window still included in that stock quantity and the gross demand forecast. Use `InventoryGateway` for the actual net-availability API described above; it handles the normalization and marks unknown commitments explicitly.

The proposed order being evaluated is not yet reserved. A newly accepted order requires a fresh snapshot for subsequent recommendations. No deliveries are counted before arrival. In v1, end the horizon at or before the next delivery or usable-life boundary; the caller must provide history for an equivalent period. `valid_until` expresses supplied horizon coverage, not a guarantee that concurrent orders cannot change stock.

## Candidate contract

Each candidate supplies the entire customized recipe, including unchanged ingredients, in `requirements`. Aggregate repeated ingredients. Do not supply only inventory deltas, and do not deduct the original recipe and modified recipe together. Use the JSON request fixture as the complete example.

Approval, customer constraints, and preparation feasibility come from verified restaurant/upstream services. The engine does not interpret allergies or infer safety. Use `null` for unknown customer constraints or preparation feasibility; that option requires staff review. An explicit `requires_staff_review: true` also blocks automatic recommendation.

## Result contract

- `needs_customer_approval`: one or more options passed checks; `recommended_option_id` is the top-ranked option. Customer acceptance and any API-level workflow flags are still required.
- `needs_staff_review`: no customer-ready options and at least one candidate has uncertain inputs or a staff-review requirement. There is no automatic recommendation.
- `unavailable`: all candidates fail known hard constraints. There is no recommendation.
- `ranked_options` contains eligible options, ordered best first. `review_options` contains scored staff-review proposals with `eligible: false`. `excluded_options` contains blocked or unresolved options and their reasons. Known restriction violations never enter either ranked list.
- Every result returns the supplied `inventory_version`, forecasts, scores, factor values, quantities and cost changes. Scores are relative ranking signals, not success probabilities.
- Forecast-related warnings are per ingredient; integration code should preserve them. Unknown forecasts have `expected_usage: null` and `uncertainty_buffer: null`.
- `explanation_source` identifies deterministic rendering or OpenRouter-selected evidence. Provider failures appear in `warnings`, without raw provider messages or secrets.

On acceptance, the API/inventory owner must re-evaluate against current stock and atomically reserve the full recipe once, using an idempotent order/confirmation ID. A cached recommendation never authorizes a stock deduction by itself. This starter module provides no endpoints, persistence, authentication, or confirmation implementation.
