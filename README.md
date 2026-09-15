# GenesysToolkit

Python toolkit for Genesys Cloud operations at ArchWell Health: **CLI scripts** for telephony, queues, users/roles, and audits, plus a **Flask Ops web app** for supervised dry-run and (local) execute workflows.

Repository folder: `GenesysCloudToolkit` (historical name). Product branding: **GenesysToolkit**.

## Quick start

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
pip install -r requirements.txt
```

Copy OAuth credentials to `client.env` (local only — never commit). Validate:

```powershell
python -m core.verify_auth
```

## Web app entry points

| Entry point | Port | Execute | Use case |
|-------------|------|---------|----------|
| `genesys_toolkit_app.py` | 8081 | No — dry run only | **Canonical demo** (Render / Azure preview) |
| `ops_app_demo.py` | 8081 | No | Backward-compatible shim → `genesys_toolkit_app` |
| `ops_app.py` | 8080 | Yes — with safeguards | Local production-style runner |
| `agent_app_demo.py` | 8082 | N/A | Agent Workspace pilot (simulated) |

**Demo (recommended for preview hosting):**

```powershell
python genesys_toolkit_app.py
```

Open **http://127.0.0.1:8081** — Ops Console, Queue Performance, Not Responding, Site Picker (`/site-picker`), Agent Workspace (`/agent`).

**Local execute mode:**

```powershell
python ops_app.py
```

Open **http://127.0.0.1:8080**

Deployment details: [README-DEMO.md](README-DEMO.md) · Agent pilot: [README-AGENT-DEMO.md](README-AGENT-DEMO.md)

Health check: **http://127.0.0.1:8081/health**

## Ops Console features

- Tool picker grouped by category (Audits, Telephony, Queues, Users/Roles, …)
- Dynamic forms with API-backed dropdowns (divisions, queues, groups)
- CSV template download where available
- **Dry run by default** — mutating tools require explicit execute confirmation
- Streaming log output with color-coded lines
- Queue Performance dashboard (OAuth supervisor login)
- Not Responding agent monitor

## CLI tools

All ops tools share a common foundation:

| Module | Purpose |
|--------|---------|
| `core/config.py` | Load `client.env`, normalize region, resolve API hosts |
| `core/genesys_client.py` | OAuth, retries, pagination |
| `core/lookups.py` | User/queue lookup helpers |
| `core/cli.py` | `--apply` dry-run pattern, CSV I/O, logging |
| `core/verify_auth.py` | Credential smoke test |

**Standard pattern:** dry run by default, pass `--apply` to make changes.

```powershell
# Read-only
python tools\users_roles\license_counts.py
python tools\queues\provisioning_snapshot.py

# Dry run (default)
python tools\queues\queue_joined_true.py --user-email user@example.com --queue-prefix "NCM_"

# Apply
python tools\telephony\patch_location_elin.py --apply
```

Tool catalog: `app/tool_specs.py` (web) · `tool_registry.py` (CLI paths)

## Scheduled reports (Windows Task Scheduler)

Weekly/biweekly jobs live in `jobs/` with registration scripts in `scripts/register_*_task.ps1`. Task names use the **GenesysToolkit-** prefix (e.g. `GenesysToolkit-WeeklyDirectOutbound`).

Manual run example:

```powershell
python jobs\weekly_direct_outbound_report.py
```

## Folder structure

```
GenesysCloudToolkit/
├── genesys_toolkit_app.py   # Canonical demo web entry
├── ops_app.py               # Local execute web entry
├── core/                    # Shared library
├── app/                     # Flask routes, tool specs, queue performance
├── tools/
│   ├── telephony/
│   ├── users_roles/
│   ├── queues/
│   ├── audits/
│   └── reporting/
├── jobs/                    # Scheduled report wrappers
├── scripts/                 # Task registration, validation, probes
│   └── probes/              # Ad-hoc API investigation scripts
├── archive/oneoffs/         # Retired one-shot fix scripts (reference only)
├── templates/               # Flask HTML
├── templates_csv/           # CSV templates for ops tools
├── config/                  # Reference CSVs (staffing, area codes)
├── docs/                    # Design docs and migration notes
├── outputs/                 # Tool output (gitignored)
├── uploads/                 # Web form uploads (gitignored)
└── client.env               # OAuth credentials (gitignored)
```

## Hosting

- **Render (demo):** `render.demo.yaml` — service name `genesys-toolkit`, start `gunicorn genesys_toolkit_app:app …`
- **Azure App Service (planned):** see `scripts/_generate_azure_hosting_doc.py` and `docs/repackaging_genesys_toolkit_20260910.md`

Set `OPS_DEMO_MODE=1` on demo hosts (set automatically by `genesys_toolkit_app.py`).

## Documentation

| Doc | Topic |
|-----|-------|
| [README-DEMO.md](README-DEMO.md) | Demo deployment, env vars, Queue Performance auth |
| [README-AGENT-DEMO.md](README-AGENT-DEMO.md) | Agent Workspace pilot |
| [docs/repackaging_genesys_toolkit_20260910.md](docs/repackaging_genesys_toolkit_20260910.md) | Rebrand + cleanup notes (2026-09-10) |
