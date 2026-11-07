# mod-sync — CLAUDE.md

DoorDash × Nova hackathon project: an assistant that turns a restaurant customer's special request
("no garlic", "swap chicken", "peanut allergy") into feasible, restaurant-approved modifications,
checks them against inventory, and ranks substitutions using demand forecasts.
Full concept: [docs/project-idea.md](docs/project-idea.md).

## Team split and ownership

| Area | Folder | Notes |
| --- | --- | --- |
| Customer & staff UI | `frontend/` (branch `UI`) | |
| Request parsing (LLM) | `backend/parsing/` | Only place that calls OpenRouter at request time. |
| Inventory, feasibility & substitute similarity | `backend/inventory/` | Deterministic. Plan: [backend/inventory/plan.md](backend/inventory/plan.md). |
| Forecast & final substitute ranking | `backend/forecast/` | Never re-implements stock or feasibility rules. |

Only edit another area's folder after agreeing with its owner.

### Request flow

1. **Parsing** turns free text into structured ops (+ allergy/diet flags).
2. **Inventory** checks the request (prep stage, allergens, diet, stock). If a substitute is needed,
   it builds the candidate list from chef-approved swaps, filters out infeasible ones, and attaches a
   similarity score to each.
3. **Forecast** takes those feasible, scored candidates, applies demand forecasts (shortage/surplus,
   cost), and returns the final ranked list of alternates.
4. Customer accepts (or staff review for allergy/diet) → **inventory** rechecks, holds/deducts stock once.

## Ground rules

- **Backend is Python** (3.11+; developed on 3.14). On Windows use `py -3`, not `python`.
- **No LLM calls in `backend/inventory/`.** Feasibility, allergen checks, quantities, prices and stock
  updates are plain, reproducible code. The system never certifies a dish as "safe" or "allergen-free".
- **Secrets:** the OpenRouter key is read from the `OPENROUTER_API_KEY` environment variable
  (`os.environ["OPENROUTER_API_KEY"]`). Never hard-code it, print it, or commit a `.env` file.
  `.env.example` documents expected variables.
- **Allergy / dietary requests always go to staff review.** The system still produces a recommendation;
  staff approve, edit, or reject. Timeout means reject.
- **Fail closed:** anything a rule doesn't explicitly allow returns an explicit non-ok status with a reason,
  never a silent pass.
- Demo data in `backend/data/` is **synthetic** and must stay labeled as such.
- Don't commit `node_modules/`, build output, or virtualenvs (see `.gitignore`).

## Contracts

Parser → inventory: a list of ops, ingredient/component IDs from `backend/data/catalog.json` only:

```python
{"op": "remove" | "add" | "substitute" | "on_side" | "less",
 "ingredient": "<ingredient or component id>",
 "qty": 1,            # add only, default 1
 "with": "<id>|None"} # substitute only; None = "suggest something"
```

Inventory entry point for the API: `inv.evaluate_request(parser_output)` returns the status
(`ready | needs_customer_approval | needs_staff_review | needs_clarification | unavailable`),
ranked alternative `options`, a recommendation, and the stock to hold. Full API and the
inventory → forecast format: [backend/inventory/plan.md](backend/inventory/plan.md#public-api).

## Commands

Run from the repo root (standard library only, no installs needed):

```bash
py -3 -m unittest discover -s tests -t .      # all tests
py -3 -m backend.inventory.demo               # walk through the demo scenarios
py -3 -m backend.inventory.demo --fixture     # regenerate fixtures/allergy_request_example.json
```

Shared example of the full allergy flow: [fixtures/allergy_request_example.json](fixtures/allergy_request_example.json).

## Keeping docs current

When you change behavior, contracts, or finish/add a task, update this file and the relevant
`plan.md` in the same change. These files are the team's (and any AI assistant's) source of truth.
