# Restaurant forecasting and modification decisions

A Python module for forecasting and routing modification requests. It forecasts ingredient demand, joins inventory availability, and decides whether a substitution, omission, or extra can be offered to the customer, needs staff review, or cannot be accommodated. Inventory owns culinary similarity scoring through OpenRouter; this module consumes those scores. Menu/catalog owners supply approved items, portions, preparation facts, and prices.

```text
Usage history → demand model → inventory coverage outlook
                                      ↓
           inventory similarity + menu prices + person-two review notes
                                      ↓
                     customer / staff / cannot accommodate
```

Demand is forecast independently of current stock. Reducing stock changes shortage exposure and extra capacity, not the predicted demand.

## Allergy and dietary safety

**An AI model cannot certify that a dish or ingredient is safe or allergen-free. Allergy and dietary restriction requests must go to restaurant staff for review before confirmation.**

Person two supplies structured restrictions and staff-review notes. Every replacement and extra must preserve those restrictions. The engine checks the entire customized recipe against verified restaurant ingredient data, blocks known violations, and treats unknown information as requiring staff review. Ingredient compatibility is not a cross-contact or preparation-safety certification.

Even when the ingredient checks pass, a restricted order is returned for staff review with person two's original notes and the explicit safety statement above. `review_options` contains ranked proposals for staff, with `eligible: false`; `recommended_option_id` remains null when there is no customer-ready option. The API owner must obtain staff review and customer acceptance before calling inventory `confirm(order)`. A note never permits an already-known violation to be approved.

Run `python -m backend.engine.demo --input fixtures/restricted_evaluation_request.json` for a synthetic restriction example. It blocks the incompatible candidate and presents the other only as a staff-review proposal. Ingredient profiles and their `verified` flags in these fixtures are fictional demo data; production must use verified restaurant records. `fixtures/connected_evaluation_request.json` illustrates the inventory adapter input; its `changes` shape is an example pending the team's actual schema.

The standalone extra-portion assessment only evaluates quantity and forecast capacity. Passing restrictions/review notes through `OutlookRequest` ensures an otherwise available extra also requires staff review. Complete-recipe restriction checking is performed by the evaluation integration.

## Run the demo

Requires Python 3.10+; Python 3.12 is recommended. Uses Pydantic 2 and StatsForecast (AutoETS). From this directory:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m backend.engine.decision_demo
python -m backend.engine.outlook_demo --extra tofu 200
python -m backend.engine.outlook_demo --extra mushrooms 200
python -m unittest discover -s tests -v
```

The included data is **synthetic**. The demo evaluates Saturday dinner, November 7, 2026, from 17:00 to 20:00 America/New_York, using twelve prior Saturdays. An extra 200 g of tofu is available now but consumes the forecast reserve. An extra 200 g of mushrooms fits within the forecast reserve. These are additional quantities: the base order must already be accounted for in inventory commitments.

See `fixtures/outlook_request.json` and `fixtures/outlook_response.json` for the upstream API example. The older substitution integration remains available with `python -m backend.engine.demo` and the `evaluation_*` fixtures. No synthetic example proves real waste or profit improvements.

## Decision and routing policy

Use `decide_modification(ModificationRequest, InventoryGateway)` for the complete modification workflow. `customer_options` and `staff_options` are the only proposals; `unavailable_options` are explanations of rejected candidates. Each proposed alternative identifies the actual item, portion, price change in cents, and customer-acceptance requirement. No call confirms an order.

The agreed minimum is **0.75 on the combined score**:

```text
combined score = 0.55 × inventory culinary similarity
               + 0.35 × forecast reserve/headroom factor
               + 0.10 × normalized incremental cost factor
```

Similarity is the largest weight. Forecast surplus can lift an otherwise less similar alternative: `0.65 × 0.55 + 1.00 × 0.35 + 0.50 × 0.10 = 0.7575`, which qualifies. These normalized weights are policy, not measured success probabilities. Reserve headroom is estimated surplus, not proof of avoidable waste.

Below 0.75, or without an inventory-provided similarity score, the replacement is not offered to either customer or staff. Return “We cannot accommodate this request with the available menu and inventory options.” There is no lower staff cutoff. Exact same-item extras and direct omissions have no replacement-similarity question; their preparation, quantity, pricing and safety checks still apply.

The cutoff uses the calculation before display rounding. A score such as 0.7499999 must not be rounded up into eligibility.

For qualifying alternatives, route to the customer only after hard checks pass, an approved price is known, forecast reserves are preserved, and there are no staff-review notes. Person two's notes and all allergy/dietary requests route to staff. Forecast reserve exposure or unresolved price/preparation/composition facts also require staff review. Known stock failures, restriction violations, nonexistent catalog items, or physically impossible separations are never acceptable staff proposals.

Catalog, approval, policy, and pricing inputs must be assembled by trusted backend services. The frontend sends the customer's request; it must not be allowed to approve its own ingredients, manufacture scores, set prices, or override staff flags.

## Substitution, omission, and extra behavior

- **Substitute:** use only a dish-approved inventory/menu option. The inventory-provided score contributes to the combined cutoff. When replacing an ingredient to avoid it, the complete final recipe must exclude it, including within mixtures. For an availability-only substitution, `exclude_target: false` can explicitly allow a prepared component containing that ingredient; allergy/dietary checks still apply.
- **Omit / “none of”:** inspect the complete recipe returned by inventory. Garlic cannot be removed from an unchanged premixed sauce. Removing the entire approved sauce or replacing it with an approved sauce verified not to contain garlic can work; the customer must see and accept that actual change. No refund is assumed for a simple omission (default price delta zero); menu-specific prices override the default.
- **Extra:** verify the final recipe equals the base recipe plus the catalog-approved extra quantities. Whole-order `qty` and extra portions are applied once. Raw avocado cannot be extracted from guacamole if inventory marks it inseparable. Extra guacamole may be offered instead if it can be portioned, its verified composition contains avocado, it passes the combined cutoff and all other checks, and its actual portion and price are shown. Do not represent 60 g guacamole as 60 g pure avocado.

Every final recipe ingredient and offered item must exist in the inventory or menu catalog. Menu listing does not imply available stock; the inventory check still needs to pass. Supplied portion requirements must correspond to the offered item, not just a label.

Omissions and substitutions must preserve unrelated ingredients. By default, the engine removes the whole target-containing component(s), then adds the approved replacement portion if any. Inventory can provide an explicit `removed_requirements` map per dish for an approved partial swap; it cannot remove unrelated ingredients or more than the original quantity. The complete recipe must equal this precise change. If inventory returns a changed recipe after the initial checks, discard that evaluation and rerun the checks; the changed recipe is not even a staff proposal yet.

The result includes per-portion quantity, dish quantity, portions per dish, total portions, total offered quantity, whole-order `requirements`, and whole-order `removed_requirements`. For example, two dishes with two 60 g extra portions each means four portions, 240 g total, and four times the per-portion menu price. Prepared-stock quantities must match the displayed quantity and canonical unit; the engine does not silently convert grams to milliliters. Raw-recipe yields for menu-only components must be supplied by inventory/catalog owners.

Every decision has `evaluated_at`, `expires_at`, and `requires_inventory_recheck_on_confirmation: true`. Validity ends at the earlier of the supplied usable-life/replenishment boundary and five minutes after evaluation by default (`max_snapshot_age_seconds` is configurable). The API must stamp evaluation time from its own clock and call `decision_is_current(decision, now)` before displaying or accepting a cached result. An expired decision needs reevaluation. This is cache freshness, not a reservation or a guarantee that stock/prices remain unchanged: final staff/customer approval, current menu pricing, and inventory `confirm(order)` remain necessary. Synthetic fixture timestamps are historical demo inputs, not live offers.

## Prices and upcharges

Use the menu-approved modifier price first. `menu_price_change_cents` is the price delta for one offered portion, backed by `menu_price_reference`. Multiply by dish quantity and extra-portion count once. Candidate/model-provided price guesses are not used as customer quotes.

For an inventory-only option without a menu price, a restaurant may explicitly configure `pricing_policy_id`, an incremental-cost absorption limit, and an approved fixed upcharge. Costs include ingredients and additional labor for the entire order. Within the authorized absorption limit, a zero price change is allowed. Above it, use the configured upcharge only when it covers the excess incremental cost beyond the allowance. Without an authorized price/rule, send a qualifying request to staff with a null price; never invent a free extra or an automatic surcharge. No real restaurant price or absorption limit has been supplied; demo fees are synthetic examples only.

The synthetic decision demo offers 60 g guacamole in response to extra avocado, using an inventory similarity of 0.65, high forecast headroom, combined score 0.7575, and a demo menu upcharge of 150 cents. See `fixtures/modification_request.json`, `modification_response.json`, and `decision_inventory.json`.

## Enable OpenRouter

Set environment variables in your backend process; `.env.example` is a reference and is not automatically loaded:

```sh
export OPENROUTER_API_KEY='your-key'
export OPENROUTER_MODEL='your-provider/model-id'
python -m backend.engine.outlook_demo --ai --extra tofu 200
```

Choose a model/provider that supports structured outputs. The integration uses asynchronous Python with a standard-library HTTPS request to OpenRouter's Chat Completions endpoint, a 15-second timeout, a JSON Schema response, and `require_parameters: true`. No additional AI SDK is required. See the official [quickstart](https://openrouter.ai/docs/quickstart) and [structured-output documentation](https://openrouter.ai/docs/guides/features/structured-outputs).

**OpenRouter's role is explanation evidence selection.** It sees calculated forecast and coverage facts, and selects their order and emphasis for each ingredient. Python validates every returned fact ID and renders the verified text. Forecast and reserve facts must be included. The downstream substitution integration uses the same pattern for option explanations. The numeric forecast comes from AutoETS or the historical baseline; OpenRouter never invents quantities or overrides their results.

**Inventory owns culinary similarity and its OpenRouter call.** Pass its score in each candidate's `similarity` with `similarity_source: "inventory"`. This module no longer calls OpenRouter to score similarity. The optional local OpenRouter client only explains computed forecast or decision facts; `python -m backend.engine.demo --ai` enables those explanations. The complete routing API requires an inventory score for an alternative, and never fabricates a replacement score or clears safety/review flags through AI.

No key is needed for the default demo. Timeouts, provider failures, malformed responses, and invented fact IDs fall back to deterministic explanations. The complete modification-routing API rejects alternatives missing inventory similarity; the lower-level evaluator flags missing data for review. Missing credentials with `--ai` produce an explicit configuration error. A live OpenRouter call has not been verified in this workspace; tests use mocked provider responses and HTTP transport.

## Integrate with the backend

Copy `backend/engine/` into your team's backend and add `pydantic>=2.7,<3` and `statsforecast>=2,<3` to the team's dependency file. Merge into the existing `backend` package; do not replace teammates' configuration. No web framework or database is imposed.

```python
from backend.engine import (
    OutlookRequest,
    OpenRouterExplainer,
    forecast_inventory,
    assess_extra,
)

# Inside your API handler, after assembling data from the other services:
async def forecast_payload(payload: dict) -> dict:
    request = OutlookRequest.model_validate(payload)
    result = await forecast_inventory(
        request,
        explainer=OpenRouterExplainer.from_env(),  # omit for offline operation
    )
    return result.model_dump(mode="json")
```

For a request such as “extra tofu,” call `assess_extra(outlook, "tofu", 200)` on the returned outlook. It reports whether stock is available, whether the portion preserves reserves, how many such extra portions fit in that reserve, and a decision: `supported`, `reserve_at_risk`, `unavailable`, or `needs_staff_review`. These are forecast/availability checks; the ordering service still validates allowed portions and prices and handles confirmation. Recheck snapshot freshness before showing a cached outlook for a new order.

For demand forecasting alone, call `forecast_usage(ForecastRequest(...))`. `adapters.py` provides an optional `InventoryReader` protocol; the API teammate fetches the snapshot and passes it into `OutlookRequest`. `forecast_inventory` runs CPU-bound fitting off the async event loop. The engine never reserves, confirms, or deducts stock.

The stable data contracts and ownership rules are in [contracts/README.md](contracts/README.md). JSON Schemas for requests and responses are supplied alongside it.

## Connect the team's inventory functions

`InventoryGateway` supports sync or async implementations of the agreed interface:

```python
requirements(dish_id, changes)  # full recipe quantities for one dish
check(dish_id, changes, qty=1)  # status, reasons, full recipe totals for qty dishes
available(ingredient_id)       # already net of quantities set aside
confirm(order)                 # inventory owns recheck and exactly-once mutation
```

```python
from backend.engine import (
    ConnectedEvaluationRequest, InventoryGateway,
    OpenRouterExplainer, evaluate_with_inventory,
)

async def evaluate_connected(payload: dict, inventory_functions) -> dict:
    # Replace these example status strings with the inventory team's actual values.
    gateway = InventoryGateway(
        inventory_functions, success_statuses={"ok"}, blocked_statuses={"unavailable"},
    )
    result = await evaluate_with_inventory(
        ConnectedEvaluationRequest.model_validate(payload), gateway,
    )
    return result.model_dump(mode="json")
```

Each connected candidate supplies `dish_id` and an opaque `changes` object. The adapter obtains full recipe quantities from inventory, scales the one-dish requirements by `qty` once, and compares them to `check`'s totals. It passes `changes` unchanged. Unit definitions come from the forecast ingredient catalog. The exact changes schema and inventory status strings still need team confirmation; success statuses must be configured explicitly. Unrecognized statuses or inconsistent quantities require staff review.

`available()` is already net. The adapter never subtracts the same set-aside amount again. If in-horizon commitment totals are optionally supplied, it reconstructs the engine's gross representation so stock is counted correctly and forecast demand excludes those known orders once. Without totals, it conservatively reserves gross forecast demand against net stock, which may overstate shortages. That limitation is returned as a warning.

The four functions do not provide historical usage, expiry/delivery timing, atomic snapshot consistency, or a snapshot version. Supply history and a restaurant-confirmed `usable_until` boundary separately. An optional inventory version is preserved; otherwise the result explicitly reports an unversioned read. Evaluation calls only `requirements`, `check`, and `available`. It never invokes `confirm`, retries a confirmation, or modifies inventory. The API owner must gate confirmation on customer/staff acceptance, call `confirm(order)`, and let inventory enforce its exactly-once behavior.

For final customer/staff routing, wrap the connected evaluation and authoritative catalog/offers in `ModificationRequest`, then call `decide_modification(request, gateway)`. The lower-level `evaluate_with_inventory` and `evaluate_substitutions` remain useful for diagnostics but do not enforce the complete menu/portion/pricing/0.75 routing policy by themselves. The four functions also do not return menu prices, flattened component composition, or similarity; inventory/menu services must supply those facts alongside them. The default routing demo uses a clearly separate fixture implementation of inventory, not your teammate's actual service.

## Forecast behavior

1. Select complete, non-stockout history with the requested ingredient, unit, source, daypart, and equivalent elapsed duration. Ignore future periods and periods beyond the lookback window.
2. Prefer the same local weekday when at least three observations exist. Otherwise use that daypart across weekdays and disclose the fallback.
3. By default, evaluate the existing [StatsForecast AutoETS model](https://nixtlaverse.nixtla.io/statsforecast/src/core/models.html#autoets) against a recency-weighted historical baseline (28-day half-life, 90-day lookback). AutoETS fits error/trend components to your usage history; it is a standard model, not a pretrained language model. Additive errors support genuine zero observations.
4. AutoETS requires at least eleven consecutive regular observations: eight for initial fitting and three for rolling validation. Each validation prediction sees only earlier observations. A gap, stockout-excluded period, or irregular cadence prevents AutoETS fitting; the baseline remains available with a warning. No fabricated zeroes are inserted. Weekly weekday-matched data uses nonseasonal ETS; daily fallback data uses weekly seasonality only when at least 21 training periods exist.
5. Select AutoETS only when its MAE is lower than the baseline on the same final three rolling validation periods. Report both MAEs and the model actually selected. This tiny selection set is not an independent accuracy test or evidence of real-world superiority. Numerical errors, missing dependencies and insufficient history explicitly retain the baseline.
6. Set a heuristic buffer from the 90th percentile of absolute validation errors, with a 10% demand floor. With fewer than three errors, use observed variation and a 25% floor. `backtest_count` and `backtest_mae` describe the errors used for the selected forecast. For successful AutoETS comparisons, these are selection-window errors; for baseline-only operation, they are chronological baseline backtest errors. These reserves are not calibrated probabilities.

Fewer than three usable observations yields an unknown forecast, not zero. An option requiring that ingredient is sent for staff review. Explicit zero-usage periods are valid and must be recorded; missing periods must not be filled with invented zeroes. Measured and recipe-estimated usage are separate sources selected explicitly by the caller.

Use `"model": "historical"` in `forecast` to force the original baseline. Reliability on real restaurant data still requires more rolling evaluation windows, interval-coverage checks, and monitoring actual usage. Weather, events, stockout-censored demand estimation, and partial-period forecasts are future work. Model support is replaceable in `models.py` if the team later chooses a pretrained time-series model.

## Inventory outlook and extras

For each ingredient, subtract in-window commitments from stock and from gross forecast demand once. `projected_balance` is stock after commitments and expected remaining demand; `reserve_headroom` also subtracts uncertainty reserve. `extra_budget_quantity = max(0, reserve_headroom)`.

The outlook classifies each ingredient as `covered`, `reserve_at_risk`, `projected_shortage`, `current_shortage` (accepted commitments already exceed stock), or `unknown`. It publishes no automatic replacement ingredients. Extra budgets are shared capacity, not a per-customer allowance and not inventory reservations. An extra that consumes reserves is flagged for downstream policy/staff decision rather than automatically rejected.

## Existing downstream substitution ranking

This older API is retained for the substitution team to consume or adapt. It is separate from the new `forecast_inventory` and `assess_extra` entry points. Culinary substitutability is an upstream attestation from the culinary service; this code does not discover it.

Hard filters require approval, confirmed customer fit, preparation feasibility, current stock after commitments, compatible units, and a fresh inventory snapshot covering the horizon. Explicit staff-review requirements and unknown feasibility/forecast information cannot produce a customer-ready recommendation.

For each ingredient in the **complete customized recipe**:

```text
available_after_commitments = max(0, available_now - committed_in_horizon)
remaining_demand = max(0, expected_usage - committed_in_horizon)
reserve = remaining_demand + uncertainty_buffer
projected_surplus = available_after_commitments - requested_quantity - reserve
reserve_score = (1 + clamp(projected_surplus / max(reserve, requested_quantity), -1, 1)) / 2
```

The gross usage forecast includes commitments. Removing them from forecast demand as well as protecting them in stock prevents double-counting. The requested customization must not already be reserved in the supplied snapshot. The recipe's lowest ingredient reserve score is its reserve factor.

The cost factor is `clamp(0.5 - (ingredient_cost_change_cents + additional_labor_cents) / (2 * cost_scale_cents), 0, 1)`. The default scale is 500 cents. The final score is a normalized weighted sum: 55% culinary similarity, 35% reserve factor, and 10% cost factor. These are illustrative, configurable weights, not validated business policy. Ties use `option_id` for stable ordering. Customer price is reported separately and is not treated as ingredient cost or profit.

Positive headroom is not proof of avoidable waste. Without batch expiry and sell-through information, the engine makes no waste-savings claim. It does not count waste savings separately or estimate full restaurant profit.

## Scope and checks

Version one supports a single service period beginning at evaluation time and ending no later than the next replenishment or relevant usable-life boundary. The inventory teammate supplies quantities usable throughout that window. It deliberately does not model deliveries or expiry events within the period; split/shorten the horizon and supply equivalent historical periods instead. Future timestamps or stale snapshots route to staff review.

The test suite covers forecast-driven recommendation changes, chronological backtesting, units, time windows, sparse history, stockouts, commitments, whole-recipe stock checks, approval/preparation constraints, input immutability, and OpenRouter validation and failure fallback. The API owner must still recheck inventory versions and atomically reserve the full customized recipe on acceptance.

Additional routing regressions cover the exact cutoff, scores just below it, recipe changes after checking, preservation of unrelated ingredients, misleading portion sizes/units, total quantities and prices across multiple dishes, mandatory review notes, and decision expiry. These checks use fixture inventory; live team-service wiring and real menu/history validation still need the team's actual data and status/change schemas.
