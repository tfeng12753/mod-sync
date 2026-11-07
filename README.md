# ModSync

ModSync is a restaurant ordering demo built for the DoorDash × Nova hackathon. Customers can request substitutions, removals, and extras in plain language. The backend checks each request against menu rules, current stock, and forecast demand. The kitchen reviews allergy and dietary requests and changes that need staff approval.

The app uses React and Vite for the customer and kitchen views, and FastAPI for the parser, inventory, and forecasting workflow. All menu, stock, and demand history data is synthetic.

## Run locally

From the repo root, install the Python dependencies and start the API:

```sh
python -m pip install -r requirements.txt
python -m uvicorn backend.api.app:app --port 8000
```

In another terminal, run `npm ci` and `npm run dev` from `frontend/`, then open [the local app](http://localhost:5173).

For a forecast demo, click **Almost out** next to avocado under **Kitchen / staff → Demo controls → Stock**. Then order a Garlic Chicken Bowl with **extra avocado** to see the forecast send it for kitchen review. Click **Restock** to restore the starting stock.

See the [API contract](docs/api-contract.md) and [Render setup](docs/render-deploy.md) for more details.
