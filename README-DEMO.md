# GenesysToolkit — Demo Deployment

Demo instance: **dry run only**. Execute mode is removed from the UI and blocked server-side even if someone tampers with the form.

## Run demo locally

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python genesys_toolkit_app.py
```

Open: **http://127.0.0.1:8081**

Legacy entry point (same app):

```powershell
python ops_app_demo.py
```

Production-style local (full execute):

```powershell
python ops_app.py
```

Open: **http://127.0.0.1:8080**

## Demo vs production entry points

| File | Port | Execute allowed |
|------|------|-----------------|
| `genesys_toolkit_app.py` | 8081 | No — preview only (**canonical**) |
| `ops_app_demo.py` | 8081 | No — shim to `genesys_toolkit_app` |
| `ops_app.py` | 8080 | Yes — with safeguards |

Demo also hides the **Delete Sites / Locations** tool (no reliable dry-run).

Each tool shows a **“What this tool does”** panel with plain-language guidance when selected.

---

## Free hosting recommendations

### 1. Render (recommended)

Best balance for a Flask demo: free tier, easy env vars, custom subdomain.

- Sign up: https://render.com
- New **Web Service** → connect Git repo (or manual deploy)
- **Start command:** `gunicorn genesys_toolkit_app:app --bind 0.0.0.0:$PORT --timeout 600`
- **Environment variables** (Dashboard → Environment) — **no `client.env` file on Render**:
  - `OPS_DEMO_MODE=1`
  - `GENESYS_CLIENT_ID` / `GENESYS_CLIENT_SECRET` — **Client Credentials** OAuth client (Queue Performance, Not Responding, ops tools)
  - `GENESYS_AGENT_CLIENT_ID` / `GENESYS_AGENT_CLIENT_SECRET` — **Authorization Code** OAuth client (site-picker sign-in only)
  - `GENESYS_REGION=usw2`
  - `FLASK_SECRET_KEY=...` (random string — site picker sessions)

These are usually **two different OAuth clients** in Genesys Admin. The agent client cannot substitute for the admin client.

- Free tier sleeps after ~15 min idle (cold start ~30s on first visit)

A starter spec is in `render.demo.yaml` (service name: `genesys-toolkit`).

**Migration from `genesys-ops-demo`:** update Render start command to `genesys_toolkit_app:app`. The old `ops_app_demo:app` shim still works if you defer the change.

### 2. PythonAnywhere

Easiest if you prefer a Python-focused host and don’t mind `yourname.pythonanywhere.com`.

- Sign up: https://www.pythonanywhere.com (free tier)
- Upload project or clone from Git
- Create a Flask web app pointing at `genesys_toolkit_app.py`
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
3. Deploy **`genesys_toolkit_app.py`** only, not `ops_app.py`.
4. Set `OPS_DEMO_MODE=1` on the host (demo entry point sets this automatically).

### Queue Performance sign-in

By default, `/queue-performance` requires **Genesys OAuth sign-in** and one of these roles:

- Supervisor
- Admin (`admin` in Genesys)
- Super Admin

Uses the same `GENESYS_AGENT_*` OAuth client as site picker (`/agent/oauth/callback` redirect).

Optional env vars:

- `OPS_QUEUE_PERFORMANCE_PUBLIC=1` — disable sign-in (public demo mode)
- `OPS_QUEUE_PERFORMANCE_ROLES=Supervisor,Admin,Super Admin` — override allowed role names (case-insensitive)

The server still uses `GENESYS_CLIENT_*` (client credentials) to fetch org-wide analytics after you sign in.

### PDF export

- **On-page:** Queue Performance → **Export PDF** (overview or current queue drill-down).
- **CLI:** `python tools/reporting/queue_performance_pdf_report.py --days 7`
- **Scheduled weekly job:** prior Mon–Sun PDF to a shared folder:
  - `python jobs/weekly_queue_performance_pdf.py --dry-run`
  - Register: `powershell -File scripts/register_weekly_queue_performance_pdf_task.ps1`
- **Shared folder env:** `QUEUE_PERFORMANCE_EXPORT_DIR=\\fileserver\share\QueuePerformance`

---

## One-time install (host or local)

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
pip install -r requirements.txt
```

`gunicorn` is included in `requirements.txt` for production hosts.
