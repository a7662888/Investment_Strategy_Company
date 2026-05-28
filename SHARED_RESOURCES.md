# Shared Resources

Canonical shared resource file for Antigravity, Claude Code, and Codex working on this app.

## Canonical Repository

- GitHub: https://github.com/a7662888/Investment_Strategy_Company
- Local path: `C:\Users\User\OneDrive\應用程式\remotely-save\Obsidian Vault\secondbrain\Codex\investment-strategy-company`
- Branch: `main`

## Runtime

- Local command: `py app.py 8770 127.0.0.1`
- LAN command: `py app.py 8770 0.0.0.0`
- Render start command: `python app.py $PORT 0.0.0.0`
- Render health check: `/api/health`
- Local URL: `http://127.0.0.1:8770`
- Temporary tunnel URL: `https://unix-legendary-douglas-anticipated.trycloudflare.com`
- Separate detected tunnel: `D:\secondbrain\投資策略顧問公司` uses `http://localhost:8501`; do not modify it from this repo unless explicitly requested.

## Do Not Commit

- `cloudflared*.log`
- `data/web_cache/`
- `.env`
- local process logs

## Project Rules

- This is a research and simulation dashboard, not an investment advisory or trading automation product.
- It does not connect to broker APIs or place live orders.
- Recommendations are after-close next-session research plans.
- Holdings are simulation inputs only.

## Agent Coordination

- Antigravity: UI, product flow, mobile experience, deployment UX.
- Claude Code: strategy rules, long-form docs, audit reports, handoff text.
- Codex: local implementation, tests, data ingestion, GitHub push, deployment wiring.

## Update This File When

- A new public deployment URL is created.
- A persistent Cloudflare tunnel or Render URL replaces the temporary URL.
- A new local port is used.
- A new data provider or cache directory is added.
- A new canonical repo or branch is selected.
