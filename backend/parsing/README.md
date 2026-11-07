# ModSync — request parser (`backend/parsing/`)

Turns a customer's free-text special request for one dish into structured changes and
allergy/diet restrictions, ready for `Inventory.evaluate_request()`. It does **not** check
inventory, forecast, price, or choose substitutes. It is the only backend module that calls
OpenRouter for parsing. Menu data is the inventory catalog (`backend/data/`, SYNTHETIC).

| File | What |
| --- | --- |
| `parser.py` | Core parser: request/result models, prompt, strict JSON schema, `validate_result`, keyword allergy/diet backstop, `parse_modifications()` (OpenRouter) |
| `adapter.py` | **`parse_request()`** — the one call the API makes: catalog → parser request → inventory input |
| `offline.py` | Deterministic rule-based fallback (no key, no network) |
| `server.py` | Standalone `POST /parse` service (Person 2's original API) |
| `run_manual_cases.py`, `manual_cases.json` | Score the real (or offline) parser on the shared cases |

## Using it from the API

```python
from backend.inventory import load_inventory
from backend.parsing.adapter import parse_request, ParserValidationError

inv = load_inventory()
parsed = parse_request(inv, "chicken_bowl", 1, "swap the chicken for tofu",
                       clarifications=[{"question": "...", "answer": "..."}])   # optional
evaluation = inv.evaluate_request(parsed.parser_output)
```

`ParsedRequest`:

| Field | Meaning |
| --- | --- |
| `status` | parser status: `ready`, `needs_clarification` (show `questions`, re-POST the original text + answers), `needs_staff_review` (allergy/diet/religious mention or an item we don't track; never auto-approve) |
| `parser_output` | `{"dish", "qty", "original_text", "ops": [...], "flags": {"level", "allergens", "diets"}}` (CLAUDE.md "Contracts") |
| `questions`, `staff_notes` | from the parser (merge with inventory's reasons) |
| `restrictions` | `[{"kind": "allergy" \| "diet", "value": "<canonical id>"}]` |
| `source` | `"openrouter"` or `"offline"` |

Errors: `ParserValidationError` = the model output was unsafe/invalid → API answers **502**
(never treat as approved). `KeyError` = unknown dish, `ValueError` = bad qty or mode,
`RuntimeError` = `mode="openrouter"` without a key or client. Empty text → `ready`, no ops, no
model call.

### Modes

- `mode="openrouter"`: LLM via OpenRouter structured output. Needs the `OPENROUTER_API_KEY`
  **environment variable** (never a committed `.env`) unless a `client` is passed (tests pass a fake
  with `.post()`). Model: `OPENROUTER_MODEL` (default `anthropic/claude-fable-5.1`; must support `json_schema`).
- `mode="offline"`: rule-based (`offline.py`): no/without/hold → remove, extra/double/add → add,
  "on the side" → on_side, light/less → less, "X instead of Y" / "swap Y for X" → substitute,
  "no Y, something else instead" → substitute with `null`, "actually ..." keeps the correction,
  words mapped with `inv.lookup()`. Same validation and safety backstop as the LLM path.
- `mode=None` (default): openrouter if a `client` is passed or `OPENROUTER_API_KEY` is set, else offline.

### Representation decisions

- **Cart:** one `CartItem` per call, `cart_item_id` = the dish id. Its recipe lists every dish
  **component** with its servings **and every ingredient inside** those components (portions =
  servings of the components containing it). So the model can target `garlic_sauce` *and*
  `garlic` / `avocado` / `chicken`; inventory decides what an ingredient-level op means
  (e.g. garlic is inside a made-ahead sauce → it offers a swap).
- **Allowed IDs:** every catalog ingredient and component, so "add tomato" on a bowl or "falafel
  instead of chicken" is reported faithfully and inventory says it isn't offered/approved.
- **Prompt payload:** the cart, allowed IDs, and `menu_vocabulary` = the dish's slice of
  `inv.parser_vocabulary()` (names, nicknames, component/ingredient kind, made-ahead notes, `in`,
  extras, swaps) + canonical allergens/diets with alias maps + the ordered quantity.
- **Ops → inventory:** remove/on_side/less → same op; add → `add` with `qty`; substitute → `substitute`
  with `with` (may be `None` = "suggest something"); `set N` vs recipe amount: `0` → remove,
  above → add the difference, below → less, equal → nothing. Identical ops are de-duplicated.
- **Unknown items:** the model lists words not on our list in `unknown_items`
  `{cart_item_id, word, operation}` (never invents an ID). The parser keeps Person 2's behavior
  (status `needs_staff_review` with a note; clear changes are kept) **and** the adapter forwards
  each one as an `"unknown:<word>"` op (spaces → `_`; `add` gets qty 1, `substitute` gets `with: None`),
  so inventory returns `needs_clarification`. An unknown *replacement* ("bacon instead of chicken")
  becomes `substitute chicken with None` + `unknown:bacon`.
- **Restrictions:** model values are normalized with the vocabulary aliases (gluten/celiac → wheat,
  peanuts → peanut). Allergens we don't stock (dairy, shellfish, tree_nut) are **kept** (inventory
  treats them as review-required). Diets the catalog doesn't track (halal, kosher, pescatarian) are
  **not** sent as diet values (inventory would mark every ingredient as conflicting); they become
  staff notes and set `flags.level = "diet"`.
- **flags.level:** `"allergy"` if any allergy restriction *or* an allergy was mentioned without a
  nameable allergen ("I have allergies"); else `"diet"` if any diet restriction or a diet/religious
  requirement was mentioned; else `None`. Any level makes inventory return `needs_staff_review`.
- **Corrections:** "no avocado. actually extra avocado" → only the correction (add). The parser
  rejects two changes to the same item as unsafe, so the fixture's "conflict" case (which passes both
  through) can't be produced by this parser; an unresolved contradiction → `needs_clarification`.

- **Follow-ups:** keep the original text and every clarification answer. In offline mode a new
  command replaces unresolved unknown/unapproved clauses; a bare known ingredient fills the
  original ambiguous target or replacement. Unrelated changes remain. "Also" adds to the request
  without discarding an unresolved item; "keep the original" withdraws that unresolved change.
  Original and earlier-answer allergy/diet concerns still pass through the deterministic backstop.
  OpenRouter receives the same complete history with explicit revision instructions. The API
  limits unsuccessful recovery to three replies, then returns a clear unavailable outcome.

## Safety

- The model must return strict JSON (`OUTPUT_SCHEMA`); `validate_result` rejects invented cart items or
  IDs, impossible ops (on_side/less on something not in the recipe, `with_ingredient_id` outside
  substitute, substitute to itself, bad quantities, duplicates) and inconsistent statuses.
- **Keyword backstop** (deterministic, checks the original text *and* clarification answers):
  allergy/intolerance/celiac/cross-contact/"X-free", religious and vegan/vegetarian mentions always
  force `needs_staff_review`, and it adds the restrictions it can detect (celiac → wheat, "vegan" →
  vegan, named allergens in an allergy sentence, "nut free" → peanut + tree_nut) even when the model
  omitted them. It is conservative: an extra restriction only means staff look more closely.
- No output is an allergy/diet safety guarantee; staff approve. Keep the key server-side.

## Standalone service

```bash
uvicorn backend.parsing.server:app --reload      # from the repo root
curl -X POST http://localhost:8000/parse -H 'Content-Type: application/json' --data-binary @backend/parsing/example_request.json
```

`POST /parse` takes Person 2's `ParseRequest` (`instructions`, `cart`, `allowed_ingredient_ids`,
`clarifications`, optional `vocabulary`) and returns `ParseResult`
(`status, changes[{cart_item_id, ingredient_id, operation, quantity, with_ingredient_id}], questions,
staff_notes, restrictions, unknown_items, safety_flags`). Operations: `add` (quantity ≥ 1),
`remove`, `on_side`, `less`, `substitute` (quantity 0), `set` (total quantity). 502 = couldn't
safely interpret. The main app (`backend/api/`) uses `parse_request()` instead.

## Tests

```bash
.venv/Scripts/python -m unittest discover -s tests -t .     # everything
.venv/Scripts/python -m pytest -q tests/parsing              # parser only
```

All offline (fake HTTP client). `tests/parsing/test_adapter.py` feeds a fake model response equal to
each `fixtures/parser_cases.json` expectation through `parse_request()` and checks the inventory
status; plus set/substitute translation, backstop restrictions, unsafe-output rejection, modes.
`test_offline.py` runs offline mode on the cases; `test_server.py` covers `/parse`.

Scoring against the real model (billable, needs the key) or offline:

```bash
.venv/Scripts/python -m backend.parsing.run_manual_cases [--offline]
```

### Offline mode limitations

Offline reaches the expected inventory status on 28/30 fixture cases (35/40 including
`manual_cases.json`). Not handled:
- `conflict` — keeps only the "actually" correction (→ `ready`), by design.
- `make-it-a-bowl` ("a bowl instead of a wrap, on greens") — names the dish, not the tortilla;
  offline sends `unknown:wrap` → `needs_clarification` (fails closed).
- `swap-base` — right status, but picks `greens_base` (from the "on greens" alias) instead of `mixed_greens`.
- `set` / totals ("3 portions in total"), "the sesame one"-style references, and anything needing context.
