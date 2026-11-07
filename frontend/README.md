# mod-sync frontend

React + Vite UI for mod-sync: a **customer app** (pick a dish, write a special request, answer
questions, pick an alternative) and a **kitchen / staff view** (review queue for allergy/diet
requests, kitchen board with pack checks, demo controls). It talks only to the backend API described
in [../docs/api-contract.md](../docs/api-contract.md). All data is synthetic.

## Run

```bash
# 1. backend (from the repo root, in another terminal)
uvicorn backend.api.app:app --reload --port 8000

# 2. frontend
cd frontend
npm install
npm run dev          # http://localhost:5173
```

Other scripts: `npm run build` (production build to `dist/`), `npm run lint`, `npm run preview`.

Orders sent from **Kitchen / staff → Demo controls** also appear in **Customer app → Your orders**
in the same browser session. For the swap scenario, click **Review options**, then **Choose this**;
the same ticket updates in the kitchen. Reviewing an order restores its dish, quantity, and request
in the customer form.

The customer form always submits orders as DoorDash, with no channel selector. Restaurant tickets
still show channel tags; reviewing an existing demo order preserves that ticket's original channel.

The listed menu price is the minimum: removals and cheaper substitutions do not lower it.
Extras and premium swaps add their full upcharges. For example, mushrooms instead of chicken
keep the chicken bowl's $14.50 base price; an extra avocado adds $2.00.

When a request needs clarification or cannot be made, enter a revised change and click
**Check updated request**. The app retains earlier replies and runs the full evaluation again.
After three unsuccessful replies it offers **Start a new request** and directs the customer to
restaurant staff for help. A valid last reply can still succeed. Lettuce exists in the demo catalog
but is not an approved extra for the chicken bowl; avocado is an approved extra for that dish.

## Environment variables

Set them in the shell or in `frontend/.env.local` (gitignored via `*.local`).

| Variable | Default | Meaning |
| --- | --- | --- |
| `VITE_API_URL` | Development: `http://localhost:8000`; production: same origin | Backend base URL; explicit empty string also uses the same origin. |
| `VITE_USE_MOCK` | unset | `1` = use the in-browser mock instead of the backend. |

## Mock mode (no backend)

```bash
VITE_USE_MOCK=1 npm run dev        # Git Bash / macOS / Linux
$env:VITE_USE_MOCK="1"; npm run dev  # PowerShell
```

`src/mockApi.js` answers every endpoint with contract-shaped data: the menu comes from
`../fixtures/menu.json`, stock mirrors `backend/data/stock.json`, and orders live in memory (reset on
page reload). It starts with one allergy order waiting for review and one confirmed order. Its
"parser" is a crude keyword imitation for UI work only; the real decisions happen in the backend.
Phrases that trigger each status:

| Status | Try |
| --- | --- |
| `ready` | "no pickled onion, sauce on the side, extra avocado" |
| `needs_customer_approval` | "can you swap the chicken", or mark an ingredient out in Demo controls |
| `needs_staff_review` | "I'm allergic to peanuts" (Satay Noodles), "I'm vegetarian" |
| `needs_clarification` | "make it like last time" |
| `unavailable` | "no miso broth" (Miso Soup) |
| HTTP 502 | "asdf" |

The mock is only bundled when `VITE_USE_MOCK=1`; a normal build never loads it.

## Code map

| File | What |
| --- | --- |
| `src/api.js` | `fetch` client for every endpoint; `ApiError` with friendly messages (502, offline). |
| `src/mockApi.js` | Dev-only mock backend. |
| `src/StoreContext.jsx`, `src/store.js`, `src/useStore.js` | Polls `GET /api/orders` (2 s), stock + health (5 s); client-only state (pack checks, handed-off marks, activity log). |
| `src/components/CustomerView.jsx` | Customer flow, rendered by evaluation `status`. |
| `src/components/StaffView.jsx`, `TicketCard.jsx` | Review queue (approve / switch option / reject), kitchen board, closed orders. |
| `src/components/SubstitutionCard.jsx` | One alternative option (customer and staff variants). |
| `src/components/DemoControls.jsx` | Sample orders, stock "mark out", demo reset. |
| `src/logic/format.js` | Pure display helpers (money, chips, pack checklist from kitchen instructions). |

Kitchen checklists and staff option instruction lists show only order changes: removals, extras,
substitutions, light portions, and items on the side. Allergy/diet labels remain on the ticket;
routine hygiene and packaging reminders are not checklist items. An unchanged order has no checks.
Order-change checks and "hand off to courier" are UI-only; the backend doesn't track them.

The root Dockerfile builds and serves this UI through FastAPI as a single Render web service.
See [Render deployment](../docs/render-deploy.md). Keep OpenRouter keys on the backend.
