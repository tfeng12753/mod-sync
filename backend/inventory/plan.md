# Inventory, feasibility & similarity — plan

Owner: Siddharth. Branch: `inventory` (merged to `main`). Status: **v1 implemented and tested
(102 unit tests): `evaluate_request()`, parser vocabulary + menu export, and a 30-case starter
parser test set. Contracts with Person 2 and Person 4 still to be agreed.**

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
| `backend/data/aliases.json` | Customer nicknames → IDs (items, dishes, allergens, diets) |
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
```

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
4. Sort options: highest weakest-swap similarity, then fewest leave-offs, then price, then cost.
   Forecast may re-rank `options`.

Output `RequestEvaluation`: `status`, `requires_staff_review`, `review_reasons`, `check` (as asked,
so the UI can explain why), `options[]` (each: `changes [{replace, with|None, similarity, source}]`,
`similarity`, price/cost deltas, `requirements`, `available`, full `check`), `reasons` (problems no
option fixes), `recommended` (index into options or `None`), `requirements` (what to hold/confirm).

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
 "similarity": 4, "price_delta_cents": 0, "cost_delta_cents": -35,
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
- [x] Unit tests for every row in the edge-case table (stdlib `unittest`)
- [x] Demo scenarios: close substitute; blocked premade removal; forecast picks between two swaps;
      stock change forces re-evaluation before confirm
- [x] `evaluate_request()` one-call entry point + `can_leave_off` catalog flag (20 more tests, 86 total)
- [x] Parser vocabulary / menu export (IDs, names, nicknames) for Person 2 and `GET /menu`
- [x] Starter parser test set (30 cases) checked against `evaluate_request()`
- [ ] Person 2 reviews/extends `fixtures/parser_cases.json` and `aliases.json`
- [ ] Synthetic usage history for forecast (`usage_log()` only covers live orders)
- [ ] Use-by dates per stock batch + "expiring before T" query (surplus story)
- [ ] Multi-dish orders: sum requirements across items into one hold
- [ ] Hook up to the API / orchestration once someone owns it

## Open questions

- Who owns API endpoints, review queue, timeout job? (Inventory already provides hold TTL + `expired_holds()`.)
- Persistence: in-memory + JSON is enough for the demo?
- Should forecast warnings apply to direct requests that are feasible (e.g. extra avocado causing a later shortage)?
- Dish and qty: assumed to come from the UI selection, not parsed from text. Confirm with Person 1/2.
- Conflicts like "no avocado. actually extra avocado": should the parser keep only the correction?
