# ModSync — Person 2 Parser

This service interprets natural-language customer modifications and sends validated structured JSON to the team's inventory module (Person 3). It does **not** check inventory, forecast, or suggest substitutions.

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Copy `.env.example` to `.env`, replace the placeholder with a **new** OpenRouter key. Do not use the leaked key from the chat. Model must support `json_schema` structured output on OpenRouter.

```bash
uvicorn main:app --reload
```

Open http://localhost:8000/docs and use `POST /parse`. Or run from this folder:

```bash
curl -X POST http://localhost:8000/parse -H 'Content-Type: application/json' --data-binary @example_request.json
```

## Integration contract

`POST /parse` request:

- `instructions`: original customer text (retain it across follow-up questions)
- `cart`: **one record per actual food item**, each with a unique `cart_item_id`, human-readable `name`, and standard `ingredients` mapping (ingredient ID → integer portion count)
- `allowed_ingredient_ids`: all recognized ingredient identifiers, including valid add-ons
- `clarifications`: previous `[{"question": ..., "answer": ...}]` pairs, default `[]`

Response:

```json
{
  "status": "ready",
  "changes": [
    {"cart_item_id": "burger-1", "ingredient_id": "onion", "operation": "remove", "quantity": 0}
  ],
  "questions": [],
  "staff_notes": []
}
```

Operations: `add` means increase by `quantity`; `remove` removes it entirely and uses `0`; `set` means total quantity; `on_side` changes presentation and uses `0`. Only forward changes to Person 3 when status is `ready`. **Person 3 must still validate allowed modification combinations, availability, and actual quantities.**

If `status == needs_clarification`, Person 1 shows questions to the customer, adds their Q/A to `clarifications`, then posts **the complete original request again**. If `status == needs_staff_review`, block auto-fulfillment and show `staff_notes` to employees. Staff-review results may also contain `questions`; these must be addressed before preparation. **Do not automatically approve a reviewed request just because a person clicked a box; restaurant staff must confirm it is safe and feasible.** `502` means the parser couldn't safely interpret the request; do not treat that as approved.

## Automated tests

```bash
pytest -q
```

Tests use fake OpenRouter HTTP responses, so they run offline without an API key. These test schema validation and API integration, **not** the actual model's understanding. For real model testing, start the API with a key and run the ten example prompts in the provided `manual_cases.json`.

## Security

- Keep the key server-side; never send it to Person 1's React frontend.
- This is a hackathon prototype, not production order safety software.
- Allergy/intolerance, religious dietary, and vegan/vegetarian requests **always** return `needs_staff_review`, even if the model says `ready`. The parser also checks customer clarification answers. Clear modifications and unresolved questions are preserved for staff to inspect. No model-generated answer is a dietary or allergy safety guarantee.
- This is a keyword-based backstop as well as an LLM instruction; additional terms/languages may require extending the patterns in `parser.py`.
- Protect the API behind authentication before connecting to actual customer information or restaurant operations.

To send the 10 cases to the real model (this makes billable requests and requires a key):

```bash
python run_manual_cases.py
```

Review the `changes` for correctness, not merely the status. Real-model behavior was not tested as part of this package.
