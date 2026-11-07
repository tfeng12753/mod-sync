# One Render web service

The root Dockerfile builds React/Vite with Node 24 and serves its output through the Python 3.12 API. The frontend uses relative `/api/...` requests, so the app needs one URL and no production CORS configuration.

## Dashboard setup

1. In [Render](https://dashboard.render.com), select **New → Web Service** and connect `tfeng12753/mod-sync`.
2. Choose branch **main** (these deployment changes are committed there).
3. Set **Language: Docker**, leave **Root Directory** blank, and use **Dockerfile Path: ./Dockerfile**. Keep the Docker command empty: the Dockerfile already supplies it.
4. Choose **Free**, set **Health Check Path: /api/health**, and deploy.
5. Open the assigned `onrender.com` URL. It should load the full UI and show **API connected**.

Alternatively, create a **Blueprint** from the same repository/branch using `render.yaml`. It creates exactly one free web service. The service follows the Blueprint's selected branch.

No `VITE_API_URL`, `CORS_ORIGINS`, separate static site, build command, or custom start command is needed for this configuration. Remove old Docker-command overrides when replacing an existing deployment. The app binds `0.0.0.0:$PORT` and runs one worker. Keep one instance because stock and orders are in memory.

## OpenRouter

In Render → service → **Environment**, optionally add:

```text
OPENROUTER_API_KEY=<new valid key entered directly in Render>
OPENROUTER_MODEL=anthropic/claude-fable-5.1
```

The parser defaults to Claude Fable 5.1 as the selected higher-capability model; the Blueprint also sets this explicitly. On an existing Render service, replace any old `OPENROUTER_MODEL` value and save/redeploy. Environment overrides take precedence over the code default.

Without a key, the existing offline parser works. Keys belong in backend runtime environment variables, never frontend `VITE_` variables or Docker build arguments. The integration notes identify an old committed key; use a new valid key. Docker excludes `.env` files and never references secret build arguments.

## Verify

- `/` serves the React app.
- `/api/health` returns `{"status":"ok"}`.
- `/api/menu` returns the real synthetic menu.
- Create a demo order, then check the same ticket in Kitchen / staff. Allergy/diet requests must retain staff review.
- **Kitchen / staff → Demo controls → Clear backend → Clear now** clears shared demo orders and holds and restores starting stock. This calls the real `/api/demo/reset` endpoint; it does not remove configuration or keys.

## Local container

```sh
docker build -t mod-sync .
docker run --rm -p 10000:10000 mod-sync
```

Open `http://localhost:10000`. For OpenRouter, export the variables in your shell and pass them by name with `-e OPENROUTER_API_KEY -e OPENROUTER_MODEL`.

This remains the synthetic-data hackathon demo. Staff/reset controls have no authentication, and stock/orders reset on process restart or redeploy. Free Render services can sleep when idle. Do not use real restaurant/customer data in this version. See [Render Docker deployment](https://render.com/docs/docker), [Blueprint fields](https://render.com/docs/blueprint-spec), and [free-service limits](https://render.com/docs/free).
