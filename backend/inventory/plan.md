# Inventory, feasibility & similarity — plan

Owner: Siddharth. Branch: `inventory`. Status: **v1 implemented and tested (66 unit tests);
contracts with Person 2 and Person 4 still to be agreed.**

Keep this file updated as tasks finish or decisions change (see `CLAUDE.md`).

## Files

| File | What it does |
| --- | --- |
| `models.py` | Dataclasses: `Ingredient`, `Component`, `Dish`, `Reason`, `CheckResult`, `Candidate`, `StockResult` |
| `catalog.py` | Load + validate `backend/data/catalog.json` (`CatalogError` lists every problem) |
| `feasibility.py` | Applies ops to a dish; prep-stage, extras, swaps, allergens, diet, stock; similarity scoring |
| `stock.py` | Thread-safe `StockStore`: available, hold (TTL), confirm (once), release, adjust, usage log |
| `service.py` | `Inventory` facade + `load_inventory()` — the only thing other modules should import |
| `demo.py` | `py -3 -m backend.inventory.demo` walks the scenarios; `--fixture` regenerates the shared fixture |
| `tests/inventory/` | One test per edge-case row, stock/hold flows, staff-review flow, demo scenarios |

## Responsibilities

1. **Catalog:** ingredients, dish components (premade vs assembly), dishes, allowed extras,
   chef-approved substitutions with similarity scores, allergen and diet tags, cross-contact notes.
2. **Requirements:** given a dish + parsed ops + quantity, compute the full stock needed.
3. **Feasibility check:** prep-stage rules, allergen exclusion, diet requirements, approved-only
   swaps and extras, stock availability. Every outcome is an explicit status with reasons.
4. **Substitute candidates + similarity:** build candidates from approved swaps, drop infeasible ones,
   attach a similarity score, send them to forecast (Person 4) for the final ranking.
5. **Stock state:** available stock, holds while staff review, confirm (recheck, deduct exactly once),
   release, manual adjustments, usage log for forecasting.
6. **Staff-review evidence:** per-component ingredient breakdown, allergen sources, cross-contact notes,
   kitchen allergy steps.

Not mine: parsing text, deciding the final ranking, UI, the forecast model.
Unassigned (team to decide): API endpoints, review-queue storage, timeout job, customer notifications.
Proposal: I own the review *state* (hold/confirm/release/timeout); endpoints go elsewhere.

## Data model

- **Ingredient** — raw stocked item: `id, name, unit (g/ml/each), allergens[], diet[]` (e.g. `vegan`,
  `vegetarian`), `cost_cents_per_unit`.
- **Component** — a part of a dish.
  - `premade`: prepared ahead and stocked as itself (e.g. `garlic_sauce`, in ml). Has `contains[]`
    for allergens/diet. Individual ingredients **cannot** be removed from it.
  - `assembly`: built per order from raw ingredients: `uses {ingredient: qty}`, `main` ingredient.
    Removing the main ingredient drops the component; removing another ingredient drops just that usage.
  - `cross_contact[]` notes (e.g. "shared fryer").
- **Dish** — `price_cents`, `components {component: servings}`,
  `extras {component: {max, price_cents}}`,
  `substitutions [{replace, with, similarity, price_delta_cents}]` (component level, chef-approved).
- **Stock** — `on_hand {item: qty}` for raw ingredients and premade components, `safety_buffer`,
  `deliveries [{item, qty, eta}]`, active `holds`, confirmed orders, `usage_log`.
- Quantities are integers in the item's unit; "less" = half, rounded up. Money is integer cents.

Sample data: `backend/data/catalog.json`, `backend/data/stock.json` — one restaurant, ~5 dishes,
~20 ingredients, labeled **synthetic**.

## Similarity scores

Each approved swap carries a `similarity` from 1–5 and a `similarity_source`. Implemented:
1. **Chef-entered** score on the approved swap (`"similarity": 4`) → source `"chef"`.
2. **Tag fallback** when a swap has no score: overlap of the two components' `tags`
   (Jaccard mapped to 1–5) → source `"tags"`. Example: `rice_base → greens_base` in `chicken_bowl`.
3. Optional stretch (not built): an LLM proposes swaps + scores **offline** for chef approval. Never at request time.

## Public API

```python
from backend.inventory import load_inventory
inv = load_inventory()                          # reads backend/data/*.json

inv.requirements(dish_id, ops, qty=1) -> dict[item_id, int]
inv.check(dish_id, ops, qty=1, exclude_allergens=(), require_diet=()) -> CheckResult
inv.substitute_candidates(dish_id, target, qty=1, exclude_allergens=(), require_diet=(),
                          base_ops=(), feasible_only=True)
    -> list[Candidate]          # sorted by similarity; base_ops = customer's other ops, kept applied
inv.available(item_id) -> int                   # on_hand - active holds
inv.hold(order_id, requirements, ttl_seconds=600) -> StockResult   # re-hold replaces atomically
inv.confirm(order_id, requirements=None) -> StockResult   # recheck, deduct once, idempotent
inv.release(order_id) -> bool
inv.expired_holds() -> list[str]                # timed-out orders since last call (= rejected)
inv.adjust(item_id, delta, reason) -> int       # deliveries, waste; never below zero
inv.set_on_hand(item_id, qty, reason) -> int    # e.g. "out of avocado" -> 0
inv.usage_log() / inv.deliveries()              # read-only, for forecast
```

Example: [fixtures/allergy_request_example.json](../../fixtures/allergy_request_example.json)
(parser output → inventory check → candidates → proposed forecast/review shapes).

**CheckResult:** `status` (`ok | blocked | out_of_stock | needs_clarification`), `reasons`
(`code, message, item, severity`), `requirements`, `price_delta_cents`, `cost_delta_cents`,
`kitchen_instructions[]`, `evidence` (components with contents, allergen sources, cross-contact,
allergy protocol), `requires_staff_review` (true when allergy/diet constraints were given).

**Candidate (inventory → forecast):**

```python
{"dish": "chicken_bowl", "replace": "grilled_chicken", "with": "crispy_tofu",
 "similarity": 4, "price_delta_cents": 0, "cost_delta_cents": -35,
 "requirements": {"tofu": 120, "rice": 200, ...},
 "available": {"tofu": 900, ...}, "check": <CheckResult>}
```

Forecast returns the same candidates in final order (plus its own scores/reasons).

## Rules and edge cases

| Case | Result |
| --- | --- |
| Unknown ID or unknown op | `needs_clarification` |
| Conflicting ops on one item (remove + extra) | `needs_clarification` |
| `substitute` with no `with` | `needs_clarification` → caller asks for candidates |
| Remove ingredient inside a premade component | `blocked` (suggest removing/swapping the whole component) |
| Remove something not in the dish | `ok` + info reason (no-op) |
| Extra not in the dish's extras list, or over `max` | `blocked` |
| Swap not chef-approved for this dish | `blocked` |
| Final dish contains an excluded allergen (incl. inside premade) | `blocked`, reason names the source |
| Final dish violates a required diet tag | `blocked` |
| Not enough available stock (after holds, minus safety buffer) | `out_of_stock` with shortfalls |
| `qty` > 1 | requirements multiplied before checking |
| Confirm called twice | second is a no-op (no double deduction) |
| Hold expired before confirm | recheck availability; fail if short |
| Staff edit | re-run `check()` on the edited ops, then replace the hold |

Status precedence: `needs_clarification` > `blocked` > `out_of_stock` > `ok`; all reasons are returned.
`info` reasons (e.g. `NOT_IN_DISH` on remove) never change the status.

## Implementation decisions

- Ops accept the target under `ingredient` (also `target`/`component`); it can be an ingredient or component ID.
- An ingredient target resolves to the component where it's the `main` ingredient; ops on an
  ingredient inside a premade component are blocked (`PREMADE_INGREDIENT`) with the approved swaps as a hint.
- Conflicting ops on the same item are **not applied** at all (`CONFLICTING_OPS`).
- Safety buffer applies in `check()` only (`LOW_STOCK` → `out_of_stock`); `hold`/`confirm` only refuse
  when stock would go negative, so a check that passed never fails at hold for buffer reasons.
- Prices: extras add their price, swaps add `price_delta_cents`; remove/less give no discount.
  `cost_delta_cents` = ingredient cost of the custom dish minus the standard dish.
- `requires_staff_review` is true whenever allergens or diets were passed in, even if the check is `ok`.
  Evidence says cross-contact is **not verified** by the system.
- Persistence is in-memory, loaded from JSON at startup (fine for the demo; restarts reset stock).

## Tasks

- [ ] Agree contracts with Person 2 (ops + allergy fields) and Person 4 (candidate format) — share the fixture
- [x] Write one full allergy-request example to `fixtures/`
- [x] Sample catalog + stock data (synthetic): 5 dishes, 30 ingredients, 20 components
- [x] Models + catalog loading/validation
- [x] `requirements()` and op application
- [x] `check()` — prep stage, extras, approved swaps, allergens, diet, stock
- [x] `substitute_candidates()` with similarity scores (chef + tag fallback)
- [x] Stock store: available, hold, confirm, release, adjust, expiry, usage log (thread-safe)
- [x] Kitchen instructions + staff-review evidence
- [x] Unit tests for every row in the edge-case table (stdlib `unittest`, 66 tests)
- [x] Demo scenarios: close substitute; blocked premade removal; forecast picks between two swaps;
      stock change forces re-evaluation before confirm
- [ ] Hook up to the API / orchestration once someone owns it
- [ ] Historical usage data for forecast (who generates it? `usage_log()` only covers live orders)

## Open questions

- Who owns API endpoints, review queue, timeout job? (Inventory already provides hold TTL + `expired_holds()`.)
- Persistence: in-memory + JSON is enough for the demo?
- Should forecast warnings apply to direct requests that are feasible (e.g. extra avocado causing a later shortage)?
- Does the parser send dish IDs from `catalog.json`, or names we need to map?
