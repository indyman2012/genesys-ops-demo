# GenesysToolkit Repackaging — 2026-09-10

Rebrand from `genesys-ops-demo` to **GenesysToolkit** and repository cleanup. No git history rewrite; backward-compatible shims retained.

## What changed

### Branding

| Before | After |
|--------|-------|
| `genesys-ops-demo` (Render service, docs) | `genesys-toolkit` / **GenesysToolkit** |
| `ops_app_demo.py` (canonical demo entry) | `genesys_toolkit_app.py` (canonical); `ops_app_demo.py` → shim |
| UI title "ArchWell Health — Ops Console" | "GenesysToolkit — Ops Console" (ArchWell logo retained) |
| Health JSON (ops only) | Adds `"app": "GenesysToolkit"`, `"service": "ops-console"` |
| Default Flask secret fallback `genesys-ops-demo` | `genesys-toolkit-demo` |

**Unchanged (intentional):**

- `OPS_DEMO_MODE` environment variable — existing Render/Azure configs keep working
- Repository folder name `GenesysCloudToolkit`
- Windows Task Scheduler prefix `GenesysToolkit-*` (already correct)
- `ops_app.py` local execute entry (port 8080)

### New / updated entry points

| File | Role |
|------|------|
| `genesys_toolkit_app.py` | **Canonical** demo web app (port 8081, dry-run only) |
| `ops_app_demo.py` | Backward-compatible shim importing `genesys_toolkit_app` |
| `ops_app.py` | Local production-style runner (execute allowed, port 8080) |

### Deployment configs updated

- `render.demo.yaml` — service `genesys-toolkit`, gunicorn target `genesys_toolkit_app:app`
- `Procfile.demo` — same gunicorn target
- `render.site_picker.yaml` — comment updated (site picker mounted on main app)

### Repository cleanup

**Moved to `scripts/probes/` (18 files):**

All root `_probe_*.py` scripts plus `_sdk_probe.py`. See `scripts/probes/README.md`.

**Moved to `archive/oneoffs/` (7 files):**

`_doug_queues.py`, `_fix_cj_walker_ext.py`, `_provision_okc_mid_del.py`, `_revert_ma_ext_reassign.py`, `_setup_clemkt.py`, `_test_phone_create.py`, `_verify_cj_ext.py`. See `archive/oneoffs/README.md`.

**Moved to `scripts/` (2 files):**

- `_validate_ops_demo.py` — web app validation harness (PROJECT_ROOT fixed to repo root)
- `_generate_azure_hosting_doc.py` — Azure hosting Word doc generator (branding updated)

**Root directory:** no remaining `_*.py` one-offs.

### Documentation

- `README.md` — rewritten as GenesysToolkit overview
- `README-DEMO.md` — updated deploy commands and Render notes
- Historical docs (`docs/ops_demo_validation_20260909.md`, etc.) — header/entry-point notes only

### `.gitignore`

Added common root-level audit/validation CSV/JSON artifacts (outputs/ was already ignored).

---

## Run commands

### Demo web app (recommended)

```powershell
Set-Location "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
pip install -r requirements.txt
python genesys_toolkit_app.py
```

Open: http://127.0.0.1:8081  
Health: http://127.0.0.1:8081/health

### Legacy shim (same app)

```powershell
python ops_app_demo.py
```

### Local execute mode

```powershell
python ops_app.py
```

Open: http://127.0.0.1:8080

### Agent Workspace pilot

```powershell
python agent_app_demo.py
```

Open: http://127.0.0.1:8082/agent

### Validate web app (harness)

```powershell
python scripts\_validate_ops_demo.py
```

### Example CLI tools

```powershell
python -m core.verify_auth
python tools\users_roles\license_counts.py
python tools\queues\provisioning_snapshot.py
python jobs\weekly_direct_outbound_report.py
```

---

## Migration notes

### Render

1. Update **Start Command** to: `gunicorn genesys_toolkit_app:app --bind 0.0.0.0:$PORT --timeout 600`
2. Optional: rename service from `genesys-ops-demo` to `genesys-toolkit` (changes URL unless custom domain)
3. Keep existing env vars (`OPS_DEMO_MODE`, `GENESYS_*`, `FLASK_SECRET_KEY`)
4. Update Genesys OAuth redirect URIs if the Render hostname changes
5. **Deferred OK:** `ops_app_demo:app` still works via shim

### Azure App Service (planned)

- Startup command: `gunicorn genesys_toolkit_app:app --bind 0.0.0.0:$PORT --timeout 600 --workers 1`
- Regenerate hosting doc: `python scripts\_generate_azure_hosting_doc.py` (requires `python-docx`)

### Windows Task Scheduler

No changes required — tasks already use `GenesysToolkit-*` names and call `jobs/*.py` directly.

### PythonAnywhere / other hosts

Point WSGI at `genesys_toolkit_app:app` instead of `ops_app_demo:app`.

---

## Files moved (summary)

| From (root) | To |
|-------------|-----|
| `_probe_*.py`, `_sdk_probe.py` | `scripts/probes/` |
| `_fix_*`, `_setup_*`, `_provision_*`, `_revert_*`, `_verify_*`, `_test_*`, `_doug_*` | `archive/oneoffs/` |
| `_validate_ops_demo.py` | `scripts/` |
| `_generate_azure_hosting_doc.py` | `scripts/` |

## New files

| File | Purpose |
|------|---------|
| `genesys_toolkit_app.py` | Canonical demo Flask entry |
| `scripts/probes/README.md` | Probe script index |
| `archive/oneoffs/README.md` | Archived one-off index |
| `docs/repackaging_genesys_toolkit_20260910.md` | This document |

---

## Git commit recommendation

**Recommended:** single commit after review, e.g.:

```
Rebrand to GenesysToolkit and clean up repo layout

- Add genesys_toolkit_app.py as canonical demo entry; keep ops_app_demo shim
- Move root probe/one-off scripts to scripts/probes/ and archive/oneoffs/
- Update README, Render/Procfile, health endpoints, UI titles
- Expand .gitignore for local audit artifacts
```

Include moved scripts in the commit so history is traceable. Do **not** commit `client.env`, `outputs/`, or root audit CSVs (now gitignored).
