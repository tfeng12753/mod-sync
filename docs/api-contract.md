# API contract (v1) — frontend ↔ backend

One FastAPI app: `uvicorn backend.api.app:app --reload --port 8000` (from the repo root).
Base path `/api`. JSON everywhere. Money in integer cents. CORS allows `http://localhost:5173`.
All data is synthetic. Frontend reads the base URL from `VITE_API_URL` (default `http://localhost:8000`).

The listed `price_cents` is the minimum per dish. Customer `price_change_cents` is the sum of
nonnegative extra and swap upcharges for the whole order, with dish quantity applied once.
Cheaper swaps and removals give no discount and cannot offset other upcharges. Null still means
staff must set a price; recipe cost savings are separate from customer prices.

`parsed.source` reports `menu` for exact catalog commands, `openrouter` for a model call,
`openrouter_cache` for a repeated validated model interpretation, or `offline` without a key.
Only parsing is cached; stock, pricing, forecasts and approval are evaluated fresh every time.

## Order lifecycle

```text
POST /api/evaluate ─► status
  ready                    → order "awaiting_customer" (customer just confirms the request as asked)
  confirmed                → plain order with blank text and no review needed (stock deducted)
  needs_customer_approval  → order "awaiting_customer" (customer picks an option)
  needs_staff_review       → order "awaiting_staff"
  needs_clarification      → no order; show questions, re-POST with clarifications
  unavailable              → no order; show customer_message
POST /api/orders/{id}/accept  (customer)  → "confirmed" (stock deducted) or "awaiting_staff"
POST /api/orders/{id}/review  (staff)     → approve / edit → "confirmed" (stock rechecked) | reject → "rejected"
POST /api/orders/{id}/cancel  (customer)  → "cancelled" (stock released)
Hold expiry (10 min)                      → "expired" (stock released)
```

## Endpoints

### `GET /api/health` → `{"status": "ok"}`

### `GET /api/menu` → `Dish[]`
Same as `fixtures/menu.json`:
`{id, name, price_cents, components:[{id,name,servings}], extras:[{id,name,max,price_cents}], swaps:[{replace,replace_name,with,with_name,price_delta_cents}], allergens:[str]}`

### `POST /api/evaluate`
Request:
```json
{"dish_id": "satay_noodles", "qty": 1,
 "text": "my kid is allergic to peanuts",
 "clarifications": [{"question": "...", "answer": "..."}],
 "channel": "doordash", "customer_name": "Sam"}
```
`clarifications`, `channel`, `customer_name` optional.

Send the original `text` and the complete ordered `clarifications` history on every follow-up.
The customer UI combines the current questions into one `question` and sends one free-text answer
per reply. Every reply reruns parsing, inventory, and (when feasible) forecast routing. A revised
command replaces the unresolved change; unrelated modifications and all safety concerns remain.
Both `needs_clarification` and `unavailable` can be revised. After three unsuccessful replies,
the response is `unavailable`, `clarification_exhausted: true`, with no order/hold and a message to
start again or contact staff. A valid third reply still proceeds. Evaluation responses include
`clarifications`, `clarification_limit: 3`, and `clarification_exhausted` for UI recovery and review.

Response `Evaluation`:
```json
{
  "order_id": "ord-0007",            // null when status is needs_clarification / unavailable
  "status": "needs_staff_review",
  "dish": {"id": "satay_noodles", "name": "Satay Noodles"},
  "qty": 1,
  "original_text": "my kid is allergic to peanuts",
  "questions": [],                   // parser + inventory clarification questions
  "parsed": {"ops": [...], "restrictions": [{"kind": "allergy", "value": "peanut"}]},
  "as_requested": {                   // the request exactly as asked
    "feasible": false,
    "reasons": ["Contains peanut: Peanut butter (in Peanut sauce)."],
    "kitchen_instructions": [],
    "price_change_cents": 0
  },
  "options": [
    {
      "option_id": "opt-1",
      "label": "Leave off Crushed peanuts; Sesame dressing instead of Peanut sauce",
      "changes": [
        {"replace": "crushed_peanuts", "replace_name": "Crushed peanuts", "with": null, "with_name": null},
        {"replace": "peanut_sauce", "replace_name": "Peanut sauce", "with": "sesame_dressing", "with_name": "Sesame dressing"}
      ],
      "route": "staff",              // "customer" | "staff"
      "score": 0.81,                 // engine combined score (0-1) or null for direct omissions
      "similarity": 0.6,
      "price_change_cents": 0,       // null = staff must set a price
      "reasons": ["..."],            // why it's offered / tradeoffs (forecast, cost)
      "staff_notes": ["..."],
      "kitchen_instructions": ["ALLERGY: peanut", "...", "NO Crushed peanuts", "SUB Sesame dressing for Peanut sauce"]
    }
  ],
  "recommended_option_id": "opt-1",
  "unavailable_options": [{"label": "...", "reasons": ["..."]}],
  "review": {
    "required": true,
    "reasons": ["allergy: peanut"],
    "staff_notes": ["Customer mentioned an allergy ..."],
    "evidence": {"components": [...], "allergens_present": {...}, "cross_contact": [...], "note": "..."}
  },
  "customer_message": "Proposed: ... The kitchen is confirming your allergy request.",
  "expires_at": "2026-11-07T18:10:00+00:00"
}
```

### `POST /api/orders/{order_id}/accept`
Customer accepts. Body: `{"option_id": "opt-1"}` or `{"option_id": null}` (accept the request as asked,
only valid when `as_requested.feasible`). Returns `Order`. If the order needs staff review it stays/moves
to `awaiting_staff`; otherwise stock is confirmed and status becomes `confirmed`.

### `POST /api/orders/{order_id}/review`
Staff action. Body:
- `{"action": "approve", "option_id": "opt-1"}` (or `null` = as requested) → confirm stock → `confirmed`
- `{"action": "edit", "option_id": "opt-2"}` → switch to another offered option, recheck stock, confirm
  (edit always ends `confirmed`, or `409` if stock changed — it never goes back to `awaiting_*`)
- `{"action": "reject", "note": "cross-contact risk"}` → release stock → `rejected`; note returned as `staff_note`
Returns `Order`.

### `POST /api/orders/{order_id}/cancel`
Customer withdraws an `awaiting_customer` / `awaiting_staff` order. Releases the hold → `cancelled`. Returns `Order`.

### `GET /api/orders` → `Order[]` (newest first)  ·  `GET /api/orders/{order_id}` → `Order`
```json
{
  "order_id": "ord-0007", "status": "awaiting_staff",
  "created_at": "...", "expires_at": "...", "channel": "doordash", "customer_name": "Sam",
  "dish": {"id": "...", "name": "..."}, "qty": 1, "original_text": "...",
  "selected_option_id": "opt-1",
  "staff_note": null,                // staff's note (e.g. reject reason)
  "kitchen_instructions": ["..."],   // for the selected option (or as requested)
  "evaluation": { ...Evaluation... },
  "history": [{"at": "...", "event": "created|accepted|approved|edited|rejected|expired", "note": "..."}]
}
```
Statuses: `awaiting_customer`, `awaiting_staff`, `confirmed`, `rejected`, `expired`, `cancelled`.

Field types: `questions` is `string[]`; `parsed.ops` items use the inventory op shape;
`parsed.restrictions` items are `{kind, value}`; `review.evidence` matches
`check.evidence` in `fixtures/allergy_request_example.json` (`components[]`, `allergens_present`
`{allergen: [source strings]}`, `cross_contact[]`, `note`). `warnings` (string[]) and `forecast`
(per-ingredient summary) may also appear on an Evaluation.

### Demo controls
- `GET /api/stock` → `{"items": [{"id", "name", "unit", "on_hand", "available", "initial_on_hand"}]}`
- `POST /api/stock/{item_id}` body `{"on_hand": 0, "reason": "ran out"}` → updated item
  (restock = post `initial_on_hand`)
- `POST /api/demo/reset` → `{"status": "reset"}`; reloads synthetic stock, clears orders

## Errors
`400` invalid input, `404` unknown order/dish, `409` invalid transition (e.g. accept an expired order),
`502` parser could not safely interpret the text (never treated as approved; no order is created, so
the UI should ask the customer to rephrase or call the restaurant). Body: `{"detail": "..."}`.

## Backend modules behind this

| Step | Module | Owner |
| --- | --- | --- |
| Text → structured changes + restrictions | `backend/parsing/` (`parse_request()` adapter) | parser |
| Feasibility, alternatives, holds/confirm | `backend/inventory/` (`evaluate_request()`) | inventory |
| Forecast + routing of alternatives | `backend/engine/` via `backend/integration/engine_adapter.py` | forecast |
| Orchestration + endpoints + order store | `backend/api/` | inventory (Siddharth) |
