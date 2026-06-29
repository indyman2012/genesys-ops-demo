# Genesys Cloud Toolkit

Unified ops tools and analytics for Genesys Cloud (ArchWell Health org).

## Ops Console (Flask UI)

Unified localhost runner for all ops tools:

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python ops_app.py
```

Open: **http://127.0.0.1:8080**

- Tool picker grouped by category (dropdown)
- Dynamic forms with API-backed dropdowns (divisions, queues, groups, queue prefixes)
- CSV template download where available
- **Dry run by default** — mutating tools show an execute section that must be explicitly enabled
- Execute requires typing `EXECUTE` + a final confirmation dialog
- Streaming log output with color-coded lines

## Ops tools rework (current focus)

All ops tools share a common foundation:

| Module | Purpose |
|--------|---------|
| `core/config.py` | Load `client.env`, normalize region, resolve API hosts |
| `core/genesys_client.py` | OAuth, retries, pagination |
| `core/lookups.py` | User/queue lookup helpers |
| `core/cli.py` | `--apply` dry-run pattern, CSV I/O, logging |
| `core/verify_auth.py` | Credential smoke test |

**Validate credentials:**
```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python -m core.verify_auth
```

**Standard tool pattern:** dry run by default, pass `--apply` to make changes.

## Tool status

| Category | Status |
|----------|--------|
| **Queues** | Reworked — shared client, fixed paths, dry-run logging |
| **Users/Roles** | Partial — `license_counts`, `role_assign` env fixed; others pending |
| **Telephony** | Partial — `patch_location_elin` reworked; others pending |
| **Audits** | Pending — still use legacy auth patterns |
| **Dashboards/MySQL** | On hold until ops tools validated |

## Folder structure

```
GenesysCloudToolkit/
├── core/                 # Shared library (start here)
├── tools/
│   ├── telephony/
│   ├── users_roles/
│   ├── queues/           # Reworked
│   └── audits/
├── tool_registry.py      # Tool catalog with corrected paths
├── client.env            # OAuth credentials
└── outputs/ payloads/    # Tool output (auto-created)
```

## Example commands

```powershell
# Read-only
python tools\users_roles\license_counts.py
python tools\queues\provisioning_snapshot.py

# Dry run (default)
python tools\queues\queue_joined_true.py --user-email user@example.com --queue-prefix "NCM_"

# Apply
python tools\telephony\patch_location_elin.py --apply
python tools\queues\queue_joined_true.py --user-email user@example.com --queue-prefix "NCM_" --apply
```

## Next steps

1. Finish telephony tool migration to `core.*`
2. Finish users_roles + audits migration
3. Rework `wrapupcodes_apply.py` (largest queue tool)
4. Wire unified CLI entry point (`python -m tools.run <tool-id>`)
5. Return to dashboards/MySQL after ops validation
