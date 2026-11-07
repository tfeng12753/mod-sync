# Inventory, feasibility & similarity — plan

Owner: Siddharth. Branch: `inventory` (merged to `main`), now developed on `integration`. Status:
**v1 implemented and tested: `evaluate_request()`, parser vocabulary + menu export, a 30-case starter
parser test set, similarity on a 0–1 scale (decision D3), synthetic usage history and use-by dates
for the forecast engine. Inventory options are routed through Person 4's `decide_modification` by
`backend/integration/engine_adapter.py`.**

Keep this file updated as tasks finish or decisions change (see `CLAUDE.md`).

## Files

| File | What it does |
| --- | --- |
| `models.py` | Dataclasses: `Ingredient`, `Component`, `Dish`, `Reason`, `CheckResult`, `Candidate`, `Option`, `RequestEvaluation`, `StockResult` |
| `catalog.py` | Load + validate `backend/data/catalog.json` (`CatalogError` lists every problem) |
| `feasibility.py` | Applies ops to a dish; prep-stage, extras, swaps, allergens, diet, stock; similarity scoring |
| `request.py` | `evaluate_request()`: parser output in → status, alternatives (combinations of swaps / leave-offs), recommendation, requirements out |
| `stock.py` | Thread-safe `StockStore`: available, hold (TTL), confirm (once), release, adjust, usage log |
| `vocabulary.py` | What the parser may output (`parser_vocabulary()`), alias `lookup()`, customer `menu()` |
| `export.py` | `py -3 -m backend.inventory.export` writes `fixtures/parser_vocabulary.json` + `fixtures/menu.json` |
| `service.py` | `Inventory` facade + `load_inventory()` — the only thing other modules should import |
| `demo.py` | `py -3 -m backend.inventory.demo` walks the scenarios; `--fixture` regenerates the shared fixture |
| `history.py` | `py -3 -m backend.inventory.history` regenerates the **synthetic** usage history; `load_usage_history()` |
| `backend/data/aliases.json` | Customer nicknames → IDs (items, dishes, allergens, diets) |
| `backend/data/usage_history.json` | Synthetic dinner usage per stock item (engine `UsageRecord` format), generated |
| `backend/data/stock.json` | On hand, safety buffers, `usable_until` (use-by per item), deliveries (ETAs with offsets) |
| `fixtures/parser_cases.json` | Starter parser test set: text → expected parser output → expected inventory status |
| `tests/inventory/` | Edge cases, stock/hold flows, staff review, demo scenarios, vocabulary, parser test set |

## Parser vocabulary and test set (for Person 2)

- **`fixtures/parser_vocabulary.json`** — put this (or the dish's slice of it) in the parser prompt.
  Has the output format, rules, ops, allergens/diets with aliases, and per dish: every item that can
  be mentioned (parts, their ingredients, extras, swap targets) with names and nicknames. Premade
  components carry a note; ingredients list which component they're `in`.
- **Unknown things:** the parser emits `"unknown:<word>"` (e.g. `unknown:bacon`, `unknown:spicy`);
  inventory returns `needs_clarification`.
- **`inv.lookup(text, dish_id)`** — deterministic word → IDs, narrowed to what's in the dish now, then
  its extras/swaps, then anything. A component beats an ingredient inside it ("no tahini" → tahini sauce).
  Useful to validate or repair LLM output.
- **`fixtures/parser_cases.json`** — 30 draft cases (nicknames, "the sauce", casual allergy mentions,
  celiac → wheat, unknown items, conflicts, premade ingredients, unmeetable diets, qty from UI).
  Input = dish + qty from the UI + free text. `tests/inventory/test_parser_cases.py` checks every
  expected output only uses vocabulary IDs and produces the listed `inventory_status`.
  Person 2 can score the real parser against the same file.
- The fixtures are generated: after editing `catalog.json` or `aliases.json`, run the export; a test
  fails if they're stale.

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
  - `can_leave_off` (sauces, toppings): the system may *suggest* dropping it as an alternative.
    Bases, proteins and broths are never suggested for removal (customers can still ask).
- **Dish** — `price_cents`, `components {component: servings}`,
  `extras {component: {max, price_cents}}`,
  `substitutions [{replace, with, similarity, price_delta_cents}]` (component level, chef-approved).
- **Stock** — `on_hand {item: qty}` for raw ingredients and premade components, `safety_buffer`,
  `usable_until {item: ISO datetime with offset}` (use-by of the batch on hand),
  `deliveries [{item, qty, eta}]` (ETA with offset), active `holds`, confirmed orders, `usage_log`.
  Naive datetimes are rejected at load (fail closed).
- Quantities are integers in the item's unit; "less" = half, rounded up. Money is integer cents.

Sample data: `backend/data/catalog.json`, `backend/data/stock.json` — one restaurant, ~5 dishes,
~20 ingredients, labeled **synthetic**.

## Similarity scores

Each approved swap carries a `similarity` on a **0–1 scale** (culinary fit; decision D3, matching
the engine's contract) and a `similarity_source`. Deterministic, no LLM call. Implemented:
1. **Chef-entered** score on the approved swap (`"similarity": 0.8`) → source `"chef"`. The catalog
   validates 0 ≤ score ≤ 1 (the old 1–5 values are rejected).
2. **Tag fallback** when a swap has no score: Jaccard overlap of the two components' `tags` mapped
   onto [0.2, 1.0] (`0.2 + 0.8 × J`, 2 decimals; no tags → 0.2) → source `"tags"`.
   Example: `rice_base → greens_base` in `chicken_bowl` = 0.47.
3. **Leave-offs** (`with: None`) have **no** similarity (`None`, source `"leave_off"`): removing a
   sauce isn't a culinary match, so no score is faked. The engine treats them as omissions.
4. Optional stretch (not built): an LLM proposes swaps + scores **offline** for chef approval. Never at request time.

Demo values (synthetic, picked for the engine's 0.75 combined cutoff = 0.55 similarity + 0.35
forecast reserve + 0.10 cost):

| Dish | Swap | Similarity |
| --- | --- | --- |
| chicken_bowl, satay_noodles | grilled_chicken → crispy_tofu / roasted_mushrooms | 0.8 / 0.65 |
| chicken_bowl | garlic_sauce → sesame_dressing | 0.7 |
| chicken_bowl | rice_base → greens_base | 0.6 (same as tofu_bowl) |
| tofu_bowl | crispy_tofu → roasted_mushrooms; sesame_dressing → garlic_sauce; rice_base → greens_base | 0.75; 0.6; 0.6 |
| satay_noodles | peanut_sauce → sesame_dressing / garlic_sauce | 0.7 / 0.55 |
| falafel_wrap | tortilla → greens_base; tahini_sauce → garlic_sauce | 0.6; 0.7 |

Mushrooms (0.65) only qualify when the forecast shows surplus (reserve factor ≈ 1): 0.55×0.65 +
0.35×1 + 0.10×0.53 ≈ 0.76. Tofu (0.8) falls below the cutoff when dinner demand eats its reserve.
A swap can never pass below similarity ≈ 0.545 (0.55×s + 0.45 at best), so every chef-approved swap
is at least 0.55. Peanut sauce is `can_leave_off: false`: it defines the satay noodles, so the system
never suggests dropping it (a customer can still ask for no peanut sauce).

## Synthetic usage history and use-by (forecast inputs)

- `backend/data/usage_history.json` (generated by `py -3 -m backend.inventory.history`, seed fixed):
  12 prior Saturday dinners (17:00–20:00 America/New_York, DST-correct offsets) before the demo
  evaluation time **2026-11-07T17:00:00-05:00** (`history.DEMO_EVALUATION_TIME`), one record per
  stocked item per dinner (zeros explicit), in the engine's `UsageRecord` format
  (`ingredient_id, unit, start, end, daypart: "dinner", quantity, source: "measured", stockout,
  complete`). Quantities = noisy synthetic dish sales (`SALES_MIX`) × catalog recipes × portion noise.
  Tofu is in high demand (dinner forecast ≈860 g vs 900 g on hand → swapping chicken for tofu eats
  the reserve); mushrooms are rarely used (forecast ≈160 g vs 2500 g → surplus). Labeled SYNTHETIC; "measured" is only the
  engine's contract label. A test fails if the file is stale.
- `stock.json` `usable_until` per item; `inv.usable_until(item_ids=None) -> {item: datetime}`.
- `inv.horizon_end(item_ids, start, latest)` = earliest of `latest`, the items' use-by, and their
  next delivery after `start` — the engine requires a horizon that spans no expiry or replenishment.
- `inv.stocked_items()`, `inv.unit_of(item)`, `inv.usage_history()` feed the engine adapter.

## Public API

```python
from backend.inventory import load_inventory
inv = load_inventory()                          # reads backend/data/*.json

inv.evaluate_request(parser_output) -> RequestEvaluation   # START HERE: one call for the API
inv.parser_vocabulary() -> dict                 # what the parser may output
inv.menu() -> list[dict]                        # GET /menu
inv.lookup(text, dish_id=None) -> list[str]     # customer word -> catalog IDs
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
inv.stocked_items() / inv.unit_of(item_id)      # forecast ingredient list + canonical units
inv.usable_until(item_ids=None) -> {item: datetime}            # use-by per batch on hand
inv.horizon_end(item_ids, start, latest) -> datetime           # min(latest, use-by, next delivery)
inv.usage_history() -> list[dict]               # synthetic engine UsageRecords (history.py)
```

Engine routing of `evaluate_request()` options: `backend/integration/engine_adapter.py`
(`await route_options(inv, evaluation)`), see [docs/integration-plan.md](../../docs/integration-plan.md#engine-changes).

Example: [fixtures/allergy_request_example.json](../../fixtures/allergy_request_example.json)
(parser output → `evaluate_request()` result → proposed forecast/review shapes).

### `evaluate_request(parser_output)`

Input (proposed parser shape):

```python
{"dish": "satay_noodles", "qty": 1, "original_text": "...",
 "ops": [{"op": "remove", "ingredient": "peanuts"}, ...],
 "flags": {"level": "allergy" | "diet" | "preference" | None,
           "allergens": ["peanut"], "diets": ["vegan"]}}      # "diet": "vegan" also accepted
```

What it does:
1. `check()` the request exactly as asked (allergens/diets from `flags`).
2. If it fails, find the components causing it: unspecified or unapproved swap, ingredient locked in
   a premade component, excluded allergen, diet conflict, out of stock / low stock. A customer's own
   swap that ran out maps back to the original component.
3. Try every combination (max 64) of chef-approved swaps for those components, plus "leave it off"
   for `can_leave_off` components; keep the customer's other ops; fully `check()` each; keep passes.
4. Sort options: options with a swap first (highest weakest-swap similarity), then fewest
   leave-offs, then price, then cost; options that only leave things off come last.
   Forecast may re-rank `options`.

Output `RequestEvaluation`: `status`, `requires_staff_review`, `review_reasons`, `check` (as asked,
so the UI can explain why), `options[]` (each: `changes [{replace, with|None, similarity (0–1, None
for a leave-off), source}]`, `similarity` (weakest swap, None if only leave-offs), price/cost deltas,
`requirements`, `available`, full `check`, `ops` = the full inventory ops that make this option),
`reasons` (problems no option fixes), `recommended` (index into options or `None`), `requirements`
(what to hold/confirm), `base_ops` (the customer's ops every option keeps).

| Status | When |
| --- | --- |
| `ready` | Works as asked, no allergy/diet flag |
| `needs_staff_review` | Allergy/diet flagged (works as asked, or options exist) |
| `needs_customer_approval` | Can't be made as asked, but approved alternatives exist |
| `needs_clarification` | Unknown item/dish/op, conflicting ops, bad qty |
| `unavailable` | Blocked with no fix (extra not offered, diet can't be met, nothing in stock) |

If any problem has no possible fix, no options are offered (status from that problem).

**CheckResult:** `status` (`ok | blocked | out_of_stock | needs_clarification`), `reasons`
(`code, message, item, severity`), `requirements`, `price_delta_cents`, `cost_delta_cents`,
`kitchen_instructions[]`, `evidence` (components with contents, allergen sources, cross-contact,
allergy protocol), `requires_staff_review` (true when allergy/diet constraints were given).

**Candidate (inventory → forecast):**

```python
{"dish": "chicken_bowl", "replace": "grilled_chicken", "with": "crispy_tofu",
 "similarity": 0.8, "similarity_source": "chef", "price_delta_cents": 0, "cost_delta_cents": -56,
 "requirements": {"tofu": 120, "rice": 200, ...},
 "available": {"tofu": 900, ...}, "check": <CheckResult>}
```

Forecast returns the same candidates in final order (plus its own scores/reasons).
`substitute_candidates()` handles one component; `evaluate_request()` options handle several at
once (e.g. leave off crushed peanuts **and** swap the peanut sauce), so forecast should prefer `options`.

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
- Prices: the menu dish price is the minimum. Extras add `max(0, price_cents)` per portion and
  swaps add `max(0, price_delta_cents)` each; remove/less give no discount. Clamp each modifier
  before summing so cheaper swaps cannot offset extras or premium swaps. Scale by dish quantity
  once. Recipe `cost_delta_cents` still reflects actual ingredient savings for forecast ranking.
  The menu export and engine adapter use the same nonnegative modifier prices.
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
- [x] Unit tests for every row in the edge-case table (stdlib `unittest`)
- [x] Demo scenarios: close substitute; blocked premade removal; forecast picks between two swaps;
      stock change forces re-evaluation before confirm
- [x] `evaluate_request()` one-call entry point + `can_leave_off` catalog flag (20 more tests, 86 total)
- [x] Parser vocabulary / menu export (IDs, names, nicknames) for Person 2 and `GET /menu`
- [x] Starter parser test set (30 cases) checked against `evaluate_request()`
- [ ] Person 2 reviews/extends `fixtures/parser_cases.json` and `aliases.json`
- [x] Similarity on a 0–1 scale (D3): chef values + tag fallback mapped to [0.2, 1.0]; leave-offs unscored
- [x] Synthetic usage history for forecast (`history.py`, `backend/data/usage_history.json`)
- [x] Use-by per stocked item (`stock.json` `usable_until`) + `usable_until()` / `horizon_end()`
- [x] Engine adapter (`backend/integration/engine_adapter.py`): options → `decide_modification`
- [ ] Use-by per *batch* (several batches of one item with different dates); today one date per item
- [ ] Multi-dish orders: sum requirements across items into one hold
- [ ] Hook up to the API / orchestration once someone owns it

## Open questions

- Who owns API endpoints, review queue, timeout job? (Inventory already provides hold TTL + `expired_holds()`.)
- Persistence: in-memory + JSON is enough for the demo?
- Should forecast warnings apply to direct requests that are feasible (e.g. extra avocado causing a later shortage)?
- Dish and qty: assumed to come from the UI selection, not parsed from text. Confirm with Person 1/2.
- Conflicts like "no avocado. actually extra avocado": should the parser keep only the correction?
