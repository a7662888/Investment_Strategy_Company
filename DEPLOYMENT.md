# Deployment Notes

## Recommended Public Setup

Use a separate GitHub repository, for example:

```text
a7662888/Investment_Strategy_Company
```

Current public deployment:

```text
https://investment-strategy-company.onrender.com
```

Do not put this app inside a medical research repository. It has a different domain, risk profile, README, and deployment lifecycle.

## Render

Render can run this Python app without extra dependencies.

One-click deploy:

```text
https://render.com/deploy?repo=https://github.com/a7662888/Investment_Strategy_Company
```

Settings:

- Runtime: Python
- Build command: empty
- Start command: `python app.py $PORT 0.0.0.0`
- Health check path: `/api/health/ready`

Render Blueprint can also read `render.yaml` from this repository.

## Phase 0 Decision Ledger

The local JSONL ledger is only an ephemeral fallback. For durable storage, create a separate private repository such as `a7662888/Investment_Strategy_Company_Data` with a `main` branch, then configure these Render secrets:

- `GITHUB_DATA_REPO=a7662888/Investment_Strategy_Company_Data`
- `GITHUB_DATA_TOKEN=<fine-grained token with Contents read/write only for the data repo>`
- `GITHUB_DATA_BRANCH=main`

Never expose the token to the browser or commit it. Until the repo and token are configured, readiness, data status, and recommendation responses report the ledger as `degraded`; a local Render write is not treated as durable.

The Render build filter ignores data-only changes under `model_artifacts/`, `data/`, `data_cache/`, and `reports/`. Code changes still deploy after CI.

Production deploys are also protected by `.github/workflows/web-smoke.yml`: pushes that touch code or tests run the full smoke suite, then call the encrypted repository secret `RENDER_DEPLOY_HOOK_URL`. Data and documentation-only commits do not enter this deploy workflow.

## Local Tunnel

For temporary phone testing:

```powershell
cloudflared tunnel --url http://127.0.0.1:8765
```

The generated `trycloudflare.com` URL is temporary. It changes when the tunnel restarts.

## Security

If the app is public, add one of:

- Cloudflare Access
- A simple login layer
- Deployment platform authentication

Even if holdings are simulated, avoid exposing private habits or watchlists unnecessarily.
