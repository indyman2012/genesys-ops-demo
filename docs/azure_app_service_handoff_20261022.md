# GenesysToolkit — Azure App Service handoff (2026-10-22, hub launcher)

Production URL example:  
`https://genesystoolkit-bhb3g5hpcvfaehcf.westus-01.azurewebsites.net`

Same surface as Render **genesys-ops-demo-3**: launchpad at `/`, ops tools at `/ops`, role-gated sections.

## Runtime

| Item | Value |
|------|--------|
| Python | **3.13** (match GitHub Actions / App Service stack) |
| Start command | `startup.sh` **or** `gunicorn genesys_toolkit_app:app --bind 0.0.0.0:$PORT --timeout 600 --workers 1` |
| Install | Oryx: `pip install -r requirements.txt` on deploy |
| Health check (recommended) | `GET /health/live` (no Genesys round-trip) |
| Full diagnostics | `GET /health` |

## Deploy source

- **GitHub Actions** (`.github/workflows/main_genesystoolkit.yml`) stages a **curated bundle** via `scripts/stage_azure_webapp.py` / `deploy/webapp_bundle.py` (not the full monorepo).
- **Disable** App Service **Deployment Center → continuous Git deploy** if still enabled, or it may overwrite Actions with an old full-repo Oryx build.

Manual zip (optional): `python scripts/package_azure_app_service.py --env-file <export.env>`

## Environment variables (App Service → Configuration)

Do **not** rely on env files in git. Set application settings in Azure (or Key Vault references).

| Variable | Purpose |
|----------|---------|
| `GENESYS_REGION` | `usw2` |
| `GENESYS_CLIENT_ID` / `GENESYS_CLIENT_SECRET` | Client credentials (ops APIs, Queue Performance when using server creds) |
| `GENESYS_AGENT_CLIENT_ID` / `GENESYS_AGENT_CLIENT_SECRET` | Authorization-code OAuth (hub sign-in, agent, site picker) |
| `GENESYS_AGENT_REDIRECT_URI` | `https://<your-host>/agent/oauth/callback` |
| `GENESYS_SITE_PICKER_REDIRECT_URI` | `https://<your-host>/site-picker/oauth/callback` |
| `OPS_DEMO_MODE` | `1` = dry-run ops console (typical demo); `0` = live tool execution |
| `OPS_QUEUE_PERFORMANCE_PUBLIC` | `0` = hub sign-in + supervisor/admin sections (default) |
| `FLASK_SECRET_KEY` | Random session secret |
| `WEBSITE_HOSTNAME` | Set automatically on App Service |

### OAuth redirect URIs (Genesys Admin)

Register for the **Azure hostname**, not Render:

- Agent / hub sign-in: `https://<host>/agent/oauth/callback`
- Site picker: `https://<host>/site-picker/oauth/callback`

## Post-deploy smoke test

1. `GET /health/live` → `"ok": true`, `"live": true`
2. `GET /` → **Apps on this server**, **Sign in with Genesys**, blue hub tiles
3. `GET /ops` → Ops Console tool catalog (**not** 404)
4. Sign in (Supervisor) → Supervision tiles (Queue Performance, Care Manager Sync, Training Roster, …)
5. Sign in (Admin) → Ops Console, Ask Genesys
6. `/call-manager-sync/` → Care Manager Sync
7. `/corporate-training/` → Training Roster

## Monthly updates

Doug delivers updated zip + this doc on a monthly cadence; routine releases can ride **push to `main`** when Actions deploy is enabled.
