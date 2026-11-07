# Inventory, feasibility & similarity — plan

Owner: Siddharth. Branch: `inventory`. Status: **planning — no code yet.**

Keep this file updated as tasks finish or decisions change (see `CLAUDE.md`).

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

Each approved swap carries a `similarity` from 1–5. Starting approach (decide during build):
1. Chef-entered score on each approved swap (simplest, used for the demo).
2. Fallback/explanation from ingredient tags (`role`, `texture`, `flavor`, `diet`): overlap score,
   used to sanity-check or fill in missing scores.
3. Optional stretch: an LLM proposes swaps + scores **offline** for chef approval. Never at request time.

## Public API

```python
from backend.inventory import load_inventory
inv = load_inventory()                          # reads backend/data/*.json

inv.requirements(dish_id, ops, qty=1) -> dict[item_id, int]
inv.check(dish_id, ops, qty=1, exclude_allergens=(), require_diet=()) -> CheckResult
inv.substitute_candidates(dish_id, target, qty=1, exclude_allergens=(), require_diet=())
    -> list[Candidate]                           # feasible only, with similarity -> forecast
inv.available(item_id) -> int                   # on_hand - active holds
inv.hold(order_id, requirements, ttl_seconds=600) -> StockResult
inv.confirm(order_id, requirements=None) -> StockResult   # recheck, deduct once, idempotent
inv.release(order_id) -> None
inv.adjust(item_id, delta, reason) -> None      # e.g. "out of avocado", deliveries
inv.usage_log() / inv.deliveries()              # read-only, for forecast
```

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

## Tasks

- [ ] Agree contracts with Person 2 (ops + allergy fields) and Person 4 (candidate format)
- [ ] Write one full allergy-request example to `fixtures/`
- [ ] Sample catalog + stock data (synthetic)
- [ ] Models + catalog loading/validation
- [ ] `requirements()` and op application
- [ ] `check()` — prep stage, extras, approved swaps, allergens, diet, stock
- [ ] `substitute_candidates()` with similarity scores
- [ ] Stock store: available, hold, confirm, release, adjust, expiry, usage log (thread-safe)
- [ ] Kitchen instructions + staff-review evidence
- [ ] Unit tests for every row in the edge-case table (stdlib `unittest`)
- [ ] Demo scenarios: close substitute; blocked premade removal; forecast picks between two swaps;
      stock change forces re-evaluation before confirm

## Open questions

- Who owns API endpoints, review queue, timeout job?
- Persistence: in-memory + JSON is enough for the demo?
- Similarity: chef-entered only, or also tag-based?
- Should forecast warnings apply to direct requests that are feasible (e.g. extra avocado causing a later shortage)?
