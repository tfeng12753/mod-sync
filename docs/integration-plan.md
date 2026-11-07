# Integration plan — connecting the 4 parts

Deployment addition: `Dockerfile` + `render.yaml` run the integrated UI/API as one Render web
service. The frontend defaults to same-origin requests in production, and FastAPI serves the
compiled UI when `FRONTEND_DIST` is set. One worker/instance preserves the in-memory demo state.
See [Render instructions](render-deploy.md).
The demo sidebar exposes **Clear backend** with confirmation; it calls `/api/demo/reset`
and refreshes orders/stock so demo state can be reset without restarting the service.

Status: **Phases 1–5 done on branch `integration` (2026-11-07).** All four parts run together:
text → parser (`backend/parsing`, offline mode without a key) → inventory `evaluate_request()` →
engine `decide_modification` via `backend/integration/engine_adapter.py` → API (`backend/api`) →
React UI (`frontend/`). 306 Python tests pass (inventory, engine, parser, integration, API, e2e);
frontend lints and builds; the demo flows were clicked through in a browser against the real API.

Demo-control orders also appear under **Customer app → Your orders** in the same browser session.
**Review options** restores the ticket's dish, quantity, request, and customer details; accepting
an option updates that same kitchen ticket.

### Still open
- Revoke the leaked OpenRouter key; test the parser once with a real key (`backend/parsing/run_manual_cases.py`).
- Team review: Person 2 (parser changes), Person 4 (one engine change, below), Jacob (UI rewrite).
- Offline parser gaps (listed in `backend/parsing/README.md`), e.g. "make it a bowl".
- State is in memory (restart = fresh stock); first evaluation takes ~3 s while the forecast model warms up.

## Decisions taken (2026-11-07)

- **Key:** `.env` deleted from `person2` (commit `d3cf7b2`). Still in git history → revoke later.
- **D1:** We write the parser changes ourselves (substitute + less ops, structured restrictions,
  catalog vocabulary) plus the adapter.
- **D2:** Use the **entire** forecast engine, including `decide_modification` routing.
- **D3–D6:** as recommended below. Python: 3.14 works (statsforecast installs fine), 3.12+ required.
- **Phase 1 done:** `integration` = `main` + `forecasting-decisions` + `person2` (moved to
  `backend/parsing/`) + `UI` (moved to `frontend/`; unrelated history, stray root `package.json`
  dropped; project doc kept). 177 tests pass.
- API shapes for the UI: [api-contract.md](api-contract.md).
- Parallel work: parser agent, engine-adapter agent, UI agent; API + orchestration by me.

## 0. Urgent: leaked API key

`origin/person2` contains a committed `.env` with a real-looking `OPENROUTER_API_KEY` (uploaded
through GitHub's web "Add files via upload", which ignores `.gitignore`). The repo is private, but
the key is now in git history for everyone with access.

- **Revoke that key on OpenRouter now** and create a new one (whoever owns it).
- Remove `.env` from the branch. Deleting it in a new commit does not remove it from history, which
  is why revoking matters more than deleting.
- Going forward: each person sets `OPENROUTER_API_KEY` as an environment variable; nobody uploads
  `.env` files.

## 1. What exists today

| Branch | Owner | What it is | Runs on |
| --- | --- | --- | --- |
| `main` = `inventory` | Siddharth (inventory) | `backend/inventory/`: catalog (5 bowl dishes), `check()`, `evaluate_request()`, holds/confirm, parser vocabulary, 102 tests | stdlib only |
| `person2` | Luksh (parser) | Root-level `parser.py` + FastAPI `main.py` (`POST /parse`), OpenRouter structured output, keyword allergy backstop | fastapi, httpx, pydantic, dotenv |
| `forecasting-decisions` | Travis (forecast) | `backend/engine/`: AutoETS/baseline forecast, inventory outlook, ranking (55% similarity / 35% reserve / 10% cost, cutoff 0.75), `decide_modification`, `InventoryGateway` adapter, JSON-schema contracts, tests | pydantic, statsforecast |
| `UI` | Jacob (UI) | React/Vite app in `nova-mods/`: customer + staff views, **its own mock menu (burgers), mock parser, mock substitutions and stock** | node |

None of the three teammates' branches has been merged; each was built against assumptions
rather than the inventory code now on `main`.

## 2. Mismatches found

### Parser (Person 2) → inventory

| Topic | Parser today | Inventory expects | Impact |
| --- | --- | --- | --- |
| Request shape | `cart` of items, each with `ingredients: {id: portions}` + `allowed_ingredient_ids` | one `dish` id + `qty` | Adapter builds the cart from the catalog |
| Ops | `add`, `remove`, `set` (total qty), `on_side` | `remove`, `add`, `substitute`, `on_side`, `less` | **No `substitute` or `less`** — "swap chicken for tofu" and "light sauce" can't be expressed. This is our core feature. |
| Allergies/diets | free-text `staff_notes` + status `needs_staff_review` | structured `flags.allergens` / `flags.diets` | Inventory can't block allergens or build safe alternatives without structured values |
| Unknown items | `needs_staff_review`, `changes: []` | `unknown:<word>` op → `needs_clarification` | Workable either way; agree on one |
| Clarifications | `needs_clarification` with questions, re-post full request | inventory also returns `needs_clarification` reasons | Two clarification sources; API should merge them |
| Multiple items | yes (`cart_item_id`) | one dish per call | API loops over cart items |
| Tests | `pytest.ini` points at `tests/`, but no tests were uploaded | — | Ask Person 2 to push them |
| Example data | burgers | bowls catalog | Needs to use `fixtures/parser_vocabulary.json` |

### Inventory → forecast (Person 4)

Person 4 wrapped an *assumed* inventory interface in `InventoryGateway`:

| Their assumption | Actual inventory | Fix |
| --- | --- | --- |
| `check()` returns `status`, `reasons: list[str]`, `requirements` | `CheckResult` with `reasons: list[Reason]` objects | Small wrapper turns reasons into message strings |
| success/blocked status strings "to configure" | `ok` / `blocked`, `out_of_stock`, `needs_clarification` | Configure gateway |
| `confirm(order)` | `confirm(order_id, requirements)` | Wrapper |
| Inventory supplies **similarity 0–1 from an OpenRouter call** | Chef scores **1–5**, deterministic (no LLM in inventory, per team decision) | Agree on scale; see decision D3 |
| One action per request (`substitute` / `omit` / `extra`, single target) | `evaluate_request()` options can combine changes (leave off crushed peanuts **and** swap peanut sauce) | Feed each inventory option as one candidate whose `changes` = the full op list. `evaluate_with_inventory` already accepts that |
| Usage **history** supplied by someone | Nobody generates it yet | Inventory to-do #2 becomes a prerequisite |
| `usable_until` horizon, `ingredient_profiles` (allergen/diet status), units, menu prices, catalog items | All exist in `catalog.json` / `stock.json` but not in their format | Generate from the catalog |
| Engine re-checks allergens/composition itself | Inventory already checks them | Fine if both read the same catalog data (profiles generated from it); inventory stays the source of truth |

Also: statsforecast pulls in numba, which may not install on Python 3.14 (what I use). Their code
falls back to the baseline forecast if the import fails, but the package is a hard dependency in
`pyproject.toml`.

### UI (Jacob) → backend

- Everything is mocked in the browser: menu (3 burgers), parser, substitution ranking, stock, timeouts.
- Order shape (`mods`, `allergens`, `subs`, `needsReview`) differs from the backend's.
- The branch **deletes `docs/project-idea.md`** and adds a stray root `package.json` (tailwind only);
  merging as-is would drop the doc.

### Missing pieces nobody owns

The API server and the code that connects everyone's parts: endpoints, a review queue, the
timeout sweep (`expired_holds()`), and turning a parser result into an inventory call and the
result into a forecast call.

## 3. Proposed target flow

```text
UI ──POST /evaluate {dish, qty, text, clarifications}──► API
  API → parser.parse()            → changes + restrictions (+ questions/staff notes)
  API → parser_adapter            → inventory ops + flags
  API → inventory.evaluate_request → status, feasible options, requirements
  API → engine (forecast ranking) → re-ranked options with forecast reasons
  API → hold stock if waiting on customer/staff → response to UI
UI ──POST /confirm or staff /review/{id}/approve|edit|reject──► API → inventory.confirm/release
UI ──GET /menu, GET /orders──► API
```

One FastAPI app (Person 2 already uses FastAPI on port 8000 with CORS for the Vite port 5173).

## 4. Decisions needed (my recommendation first)

- **D1. Parser contract.** Recommended: I write an adapter on our side for the shape differences (cart,
  `set` → add/less/remove), **and** Person 2 adds three things: a `substitute` op (`with` may be null),
  a `less` op, and a structured `restrictions` list (`{kind: allergy|diet, value}`), using the IDs in
  `fixtures/parser_vocabulary.json`. Alternative: adapter only, deriving allergens from the free text
  with keywords (fragile, and still no swaps).
- **D2. How far to use Person 4's engine.** Recommended for the demo: use `forecast_inventory` (outlook)
  and `evaluate_with_inventory` (ranking), passing inventory's feasible options as candidates.
  Skip `decide_modification` for now, because it needs catalog items, offers and menu-price
  references for every candidate, duplicating the catalog. Could be added later.
- **D3. Similarity scale.** The engine uses 0–1 with a 0.75 cutoff and 55% weight on similarity. With
  a naive 1–5 → 0–1 mapping, mushrooms (3/5) can never pass the cutoff, which kills the main demo
  story ("forecast surplus makes mushrooms win"). Recommended: store chef similarity directly as
  0–1 in the catalog (e.g. tofu 0.8, mushrooms 0.65), matching their contract, and keep it
  deterministic (no OpenRouter call). The team should agree on the cutoff/weights.
- **D4. One menu.** Recommended: the inventory catalog (bowls) is the only menu. Person 4's fixtures
  already use tofu/mushrooms/rice. UI and parser examples switch from burgers to `GET /menu` /
  `parser_vocabulary.json`.
- **D5. Repo layout.** Recommended: Person 2's files move to `backend/parsing/` (FastAPI app to
  `backend/api/`), Person 4 stays in `backend/engine/`, UI moves to `frontend/` (or stays in
  `nova-mods/`). One `requirements.txt` at the root. Python 3.12 for everyone (statsforecast support).
- **D6. Who builds the API + glue.** Recommended: me, since `evaluate_request()` already does most of
  the orchestration and the adapters touch inventory's contract. Teammates review.

## 5. Phases

**Phase 0 — today, no code**
- Revoke the leaked key; Person 2 deletes `.env` from the branch and pushes their tests.
- Team agrees D1–D6.

**Phase 1 — combine branches without behavior changes** (new branch `integration`)
- Merge `forecasting-decisions`, `person2`, `UI` into `integration`; move files per D5; keep
  `docs/project-idea.md`; drop the stray root `package.json`; one `.gitignore` and `requirements.txt`.
- Every existing test suite still passes (inventory 102, engine tests, parser tests once pushed).

**Phase 2 — adapters** (each with offline tests, no real LLM calls)
- `parser_adapter`: catalog → parser `cart`/`allowed_ingredient_ids`; parser result → inventory ops +
  flags. Test: run my 30 `parser_cases.json` through fake parser responses.
- `engine_adapter`: inventory functions for `InventoryGateway` (reason strings, `confirm` shape,
  status config); inventory options → engine candidates; ingredient units/profiles from the catalog.
- Inventory additions: similarity on 0–1 (D3), synthetic usage history (to-do #2), use-by dates →
  `usable_until` (to-do #3).

**Phase 3 — API + orchestration** (`backend/api/`)
- `GET /menu`, `POST /evaluate`, `POST /confirm`, `POST /review/{id}/approve|edit|reject`, `GET /orders`,
  background timeout sweep. Merges parser and inventory clarification questions. In-memory store.

**Phase 4 — UI wiring** (Jacob, with my help on the API shapes)
- Replace the mock menu/parser/substitutions/stock with API calls; staff view uses review endpoints.

**Phase 5 — end-to-end**
- Offline E2E tests (fake LLM) for the 4 demo scenarios + the allergy flow; one live run with a real key.

## 6. What would change in existing code

Small, in my area: similarity scale in `catalog.json` + tests; maybe a `confirm(order)` compatibility
method. Asked of teammates: Person 2 (substitute/less/restrictions, tests, remove `.env`, move files),
Person 4 (no logic changes expected; similarity/threshold agreement; dependency on Python 3.12),
Jacob (swap mocks for API calls; don't delete the project doc).
Bigger moves needing a yes first: moving Person 2's files, changing the similarity scale, choosing
not to use `decide_modification` yet, and replacing the UI's mock logic.

## Engine changes

Made by the engine-adapter work (`backend/integration/engine_adapter.py`), 2026-11-07. One change,
backward compatible (default off); all original engine tests still pass. Tests:
`tests/integration/test_engine_changes.py`.

**`ModificationOffer.removed_requirements_verified: bool = False`** (`backend/engine/decisions.py`).

- Why: the engine models a removal as "remove the stock items that contain the target". Inventory
  dish components are built from shared raw ingredients, so replacing `grilled_chicken`
  (chicken 150 g + oil 5 ml) with `crispy_tofu` (tofu 150 g + cornstarch 10 g + oil 10 ml) must remove
  5 ml of oil, which does not "contain chicken"; and a combined allergy option (leave off
  `crushed_peanuts` **and** swap `peanut_sauce` → `sesame_dressing`) removes two components while the
  engine has one `target_ingredient_id`. Without the flag the engine blocks both as "removes an
  unrelated ingredient".
- What: when an offer supplies explicit `removed_requirements` and sets the flag, inventory (the
  recipe source of truth) attests those quantities belong to the replaced/omitted components, so a
  removed item that does not itself contain the target is accepted. Every other check is unchanged:
  removal cannot exceed the original recipe, the final recipe must equal
  original − removed + portion requirements exactly, the target must be absent from the final recipe
  (unless `exclude_target: false`), restrictions/profiles, cutoff, pricing and routing all apply.
- Guard rails: the flag requires non-empty `removed_requirements` (validation error otherwise), and
  at least one removed item must still contain the target, else the option is blocked.
- `contracts/modification_request.schema.json` regenerated (`python -m backend.engine.export_contracts`);
  no other contract changed.

How the adapter uses the engine (no other engine changes):

- One engine candidate per inventory option (`option_id = "opt-<index>"`); options sharing
  `(action, target, exclude_target)` share one `decide_modification` call.
- `action`: `substitute` if the option has any swap, else `omit` (leave-offs: no similarity question).
  The primary change (weakest swap, or first leave-off) gives `target_ingredient_id` (premade
  component → its stock id, assembly component → its main ingredient) and `offered_item_id`.
- `removed_requirements` / `portion_requirements` come from inventory: original recipe vs the recipe
  with the changed components removed vs the option's recipe; `removed_requirements_verified` is set.
  Menu price = sum of the catalog swaps' `price_delta_cents`, reference
  `catalog:<dish>:<replace>-><with>`; leave-off-only options use the engine's omission policy (0).
- `changes` given to the inventory functions: `{"ops", "qty", "exclude_allergens", "require_diet"}`
  (a bare ops list also works); `requirements` returns per-dish quantities.
- Catalog items: every stocked item (`in_inventory`) and every dish component (`on_menu`), composition
  verified from the catalog. Allergy/diet flags → `restrictions` + catalog-generated
  `ingredient_profiles` (`verified: true`); they always route to staff with the safety note.
- Forecast: synthetic history (`backend/data/usage_history.json`), window
  `[now, min(now + 3 h, use-by, next delivery))`, `now` defaulting to the demo time
  2026-11-07T17:00-05:00; active holds are passed as `committed_in_horizon`. A window shorter than
  the 3 h history periods gets no forecast, so the engine fails closed. `decide_modification` takes
  no explainer; an explainer passed to `route_options` is only used for the as-asked outlook.
- A request that works as asked (no options) uses `forecast_inventory` + `assess_extra` for extras
  (the whole order's quantity of the extra item: conservative, since this order isn't committed yet).
  Reserve risk on a plain request is informational; an extra that eats the reserve goes to staff.
