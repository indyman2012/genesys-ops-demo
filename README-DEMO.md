# Genesys Cloud Ops — Demo Deployment

Demo instance: **dry run only**. Execute mode is removed from the UI and blocked server-side even if someone tampers with the form.

## Run demo locally

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python ops_app_demo.py
```

Open: **http://127.0.0.1:8081**

Production-style local (full execute):

```powershell
python ops_app.py
```

Open: **http://127.0.0.1:8080**

## Demo vs production entry points

| File | Port | Execute allowed |
|------|------|-----------------|
| `ops_app_demo.py` | 8081 | No — preview only |
| `ops_app.py` | 8080 | Yes — with safeguards |

Demo also hides the **Delete Sites / Locations** tool (no reliable dry-run).

Each tool shows a **“What this tool does”** panel with plain-language guidance when selected.

---

## Free hosting recommendations

### 1. Render (recommended)

Best balance for a Flask demo: free tier, easy env vars, custom subdomain.

- Sign up: https://render.com
- New **Web Service** → connect Git repo (or manual deploy)
- **Start command:** `gunicorn ops_app_demo:app --bind 0.0.0.0:$PORT --timeout 600`
- **Environment variables** (Dashboard → Environment) — **no `client.env` file on Render**:
  - `OPS_DEMO_MODE=1`
  - `GENESYS_AGENT_CLIENT_ID=...` (OAuth client — used for sign-in **and** server API calls)
  - `GENESYS_AGENT_CLIENT_SECRET=...`
  - `GENESYS_REGION=usw2`
  - `FLASK_SECRET_KEY=...` (random string — required for site picker sessions)
  - Optional: `GENESYS_CLIENT_ID` / `GENESYS_CLIENT_SECRET` (local-style names; same values as agent vars if you prefer)
- Free tier sleeps after ~15 min idle (cold start ~30s on first visit)

A starter spec is in `render.demo.yaml`.

### 2. PythonAnywhere

Easiest if you prefer a Python-focused host and don’t mind `yourname.pythonanywhere.com`.

- Sign up: https://www.pythonanywhere.com (free tier)
- Upload project or clone from Git
- Create a Flask web app pointing at `ops_app_demo.py`
- Set env vars in the WSGI config or a `.env` loader
- Free tier: one web app, limited CPU — fine for demos

### 3. Fly.io

More control, small free allowance, good if you already use containers.

- Requires a `Dockerfile` (not included yet)
- Better for always-on demos than Render free tier

### Not recommended for this app

- **Vercel / Netlify** — serverless; poor fit for long-running subprocess tool runs
- **GitHub Pages** — static only

---

## Security tips for public demo

1. Use a **separate OAuth client** in Genesys with **read-only** scopes where possible.
2. Never commit `client.env` — use host environment variables only.
3. Deploy **`ops_app_demo.py`** only, not `ops_app.py`.
4. Set `OPS_DEMO_MODE=1` on the host (demo entry point sets this automatically).

---

## One-time install (host or local)

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
pip install -r requirements.txt
```

`gunicorn` is included in `requirements.txt` for production hosts.
