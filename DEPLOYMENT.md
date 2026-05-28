# Deployment Notes

## Recommended Public Setup

Use a separate GitHub repository, for example:

```text
a7662888/Investment_Strategy_Company
```

Do not put this app inside a medical research repository. It has a different domain, risk profile, README, and deployment lifecycle.

## Render

Render can run this Python app without extra dependencies.

Settings:

- Runtime: Python
- Build command: empty
- Start command: `python app.py $PORT 0.0.0.0`
- Health check path: `/api/health`

Render Blueprint can also read `render.yaml` from this repository.

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
