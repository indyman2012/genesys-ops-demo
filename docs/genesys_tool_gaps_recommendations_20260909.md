# Genesys Tool Gap Recommendations

**Date:** 2026-09-09  
**Project:** GenesysCloudToolkit  
**Source:** Inventory of `tools/`, `jobs/`, `app/`, root one-offs (`_probe_*`, `_setup_*`, `_provision_*`, `_fix_*`), and agent transcript [902ef0d8-6c13-4dbc-8307-f33b95d4082a](902ef0d8-6c13-4dbc-8307-f33b95d4082a).

---

## Executive summary

The toolkit has a strong **ops execution** layer (queue membership, role assignment, telephony bulk updates, weekly reporting jobs) and a growing **Ops Demo** web runner (`app/tool_specs.py` — 30 registered tools). Repeated real-world work still falls through to **root one-off scripts** and **agent-driven ad-hoc runs**, especially for:

1. **Market site lifecycle** (location → site → outbound route → caller ID → HQ phone moves)
2. **End-to-end user onboarding** (roles + queue joined=false + WebRTC phone + extension + Primary Voice)
3. **Telephony profile compliance** (MA WORK extension, Primary Voice, duplicate extensions)
4. **Sales HQ phone → market site** workflows (audit → HR template → apply)
5. **Registry stubs** that still point at `generic_tool.py` placeholder

Highest ROI: promote proven one-offs into parameterized CLI tools, wire them into Ops Demo, and add **post-create verification** (CLEMKT outbound-route lesson).

---

## What exists today

### Ops Demo (`app/tool_specs.py`) — registered & runnable

| Category | Tools |
|----------|-------|
| Queues | joined true/false, remove user, members export, wrap-up codes, assign group to Patient queues, external contacts |
| Telephony | create sites CSV, create/update outbound routes, bulk update sites, patch ELIN, audit pre-delete, reassign phones HQ, delete sites |
| Users/Roles | license counts, role division assignment, CX license review, roaming agent role, CX1 test role, add generated emails |
| Audits | provisioning snapshot, permission audit, callback SLA, validate user site data, cleanup attachment audit |
| Other | extensions by title, create new center (preflight only) |

### CLI-only tools (implemented, **not** in Ops Demo)

| Script | Purpose |
|--------|---------|
| `tools/telephony/set_site_caller_id_from_location.py` | Site caller ID from location ELIN |
| `tools/telephony/export_hq_phone_hr_template.py` | HR review template for HQ phone site assignments |
| `tools/telephony/apply_hq_phone_site_assignments.py` | Apply HR-reviewed HQ → market site moves |
| `tools/telephony/reassign_hq_phones_by_active_queue.py` | Auto-move HQ phones by joined=true queue |
| `tools/users_roles/medical_assistant_telephony_align.py` | MA WORK ext + Primary Voice = WORK |
| `tools/users_roles/oneoff_ma_primary_voice_other.py` | MA Primary Voice → Teams OTHER (opposite policy) |
| `tools/audits/medical_assistant_provisioning_audit.py` | MA missing Communicate - User / phone |
| `tools/audits/sales_hq_phone_audit.py` | Sales queue members on HQ phones |
| `tools/audits/station_name_audit.py` | Station name ≠ `{Name} Web` |
| `tools/audits/other_phone_site_mismatch_audit.py` | Teams OTHER area code vs site market |
| `tools/audits/org_cleanup_audit.py` | Org orphan inventory (queues, flows, routes, etc.) |

### Web features outside Ops Demo tool runner

| Feature | Location |
|---------|----------|
| Queue Performance dashboard + PDF export | `app/queue_performance.py`, `jobs/weekly_queue_performance_pdf.py` |
| Not Responding live view | `app/not_responding.py` (from presence API probing) |
| Site Picker (agent self-service phone site change) | `app/site_picker/` |
| Agent Workspace pilot | `app/agent_workspace/` |

### Registry stubs (Ops Demo shows tool, runs placeholder)

These entries in `tool_registry.py` / templates still route to `tools/queues/generic_tool.py`:

- Create Sites / Locations (legacy path — superseded partially by `create_sites_from_csv.py`)
- Assign Phones & Sites
- Division Manager
- Location Code → Division
- Queue Verification

### Root one-offs indicating repeated manual work

| Script | Pattern | Canonical tool? |
|--------|---------|-----------------|
| `_setup_clemkt.py` | Market site setup (Cleveland/CLEMKT) | **No** — hardcoded |
| `_probe_clemkt_template.py` | Export live site config as template | **No** |
| `_provision_okc_mid_del.py` | 5-user OKC onboarding bundle | **No** — hardcoded names |
| `_fix_cj_walker_ext.py` / `_verify_cj_ext.py` | Duplicate extension resolution | **No** |
| `_revert_ma_ext_reassign.py` | Rollback bad MA align run | **No** |
| `_probe_okc_queues.py` / `_probe_okc_phone.py` | Queue membership API debugging | **No** |
| `_probe_genesis_ibarra.py` | Per-user telephony diagnostic | **No** |
| `_probe_presence_batch.py` / `_probe_presence_per_user.py` | Presence API exploration | Led to `not_responding.py` |
| `_test_phone_create.py` | WebRTC phone POST pattern | **No** |
| `_probe_staffing_metrics.py` | Analytics metric validation | Partially absorbed into staffing report |
| `_probe_brynne_*.py`, `_probe_cj_context.py`, `_probe_charity.py` | User-specific investigations | **No** |

---

## Incident / task evidence (transcript)

| Date / task | What happened | Tool gap |
|-------------|---------------|----------|
| **2026-09-04** — Create Cleveland location, CLEMKT site, move Sales CLE HQ phones | `_setup_clemkt.py` one-shot; 16 phones moved | No parameterized market-site wizard; outbound route reported "present" but **trunk/enabled not verified** until manual fix (CLEMKT lesson) |
| **2026-09-04** — OKC Mid-Del: Roya White, Nicole Jones, Charity Walker, Christy Stallings, Nicole Snider | `_provision_okc_mid_del.py`: roles, joined=false, WebRTC, extension, Primary Voice | No generic "onboard users to queue" orchestrator; `_probe_okc_*` used to debug membership APIs |
| **2026-09-04** — CJ Walker duplicate extension 15152 | `_fix_cj_walker_ext.py` | No org-wide duplicate extension audit/resolver |
| **2026-08~** — Genesis Ibarra outbound failures | `_probe_genesis_ibarra.py`; missing WORK ext, Primary Voice on Teams | MA align tool exists but not scheduled/UI; no per-queue telephony peer comparison |
| **2026-08~** — Sales HQ phone global audit after CLEMKT | `sales_hq_phone_audit.py` created; HR template workflow | Tools exist CLI-only; not in Ops Demo |
| **Earlier** — MA Primary Voice flip (Work → Other) | `oneoff_ma_primary_voice_other.py` | **Conflicts** with later MA align (WORK) policy — needs title-based **policy profiles**, not opposing one-offs |
| **Earlier** — MA align bad run | `_revert_ma_ext_reassign.py` | No built-in rollback from align CSV |
| **Earlier** — Org cleanup | `org_cleanup_audit.py` | CLI-only; 90 findings first run |
| **Earlier** — Not Responding dashboard | Presence API probes → `app/not_responding.py` | Delivered; consider queue-scoped variant |

---

## Recommended new tools

Priority key: **P1** = blocks recurring ops / caused production incident; **P2** = high repeat, reduces agent time; **P3** = nice-to-have / visibility.

---

### 1. Market Site Setup Wizard

**Name:** `market_site_setup.py`  
**Purpose:** End-to-end creation of a market location + 6-char site + verified outbound route + optional queue-based HQ phone moves.

**Problem it solves:** CLEMKT creation (2026-09-04) required `_setup_clemkt.py` with hardcoded CLEJEF template, caller ID, queue name, and site code. Post-create, Default Outbound Route existed but was **not operational** (missing external trunk / not enabled) until manually corrected.

**Proposed implementation:**

| Aspect | Detail |
|--------|--------|
| Surface | **Ops Demo UI** (wizard form) + CLI |
| Inputs | Template location name, new location name, site code (6 char), caller ID E.164, optional queue name(s) for HQ phone moves, trunk name (default Pure-IP) |
| Key APIs | `POST /api/v2/locations`, `POST /api/v2/telephony/providers/edges/sites`, `POST .../sites/{id}/outboundroutes`, `GET .../outboundroutes/{id}` (verify), `PUT .../phones/{id}` |
| Outputs | CSV + JSON verification block: location ID, site ID, caller ID, route enabled, trunk assigned, phones moved/skipped |
| Verification steps (required) | 1) Route record exists 2) `enabled=true` 3) `externalTrunkBases` or `trunkBaseIds` non-empty 4) Site caller ID matches input 5) Location ELIN present |

**Risk:** **High** — creates telephony infrastructure.  
**Guardrails:** Dry-run default; `--apply` requires confirmation token; idempotent (skip if location/site exists); post-apply verification fails the run if route not operational; never delete template location.

**Priority:** **P1**

**Notes:** Generalize `_setup_clemkt.py` + `_probe_clemkt_template.py` export as `--dump-template SITE_CODE` mode. Chain `set_site_caller_id_from_location.py` optionally after ELIN patch.

---

### 2. Outbound Route Health Audit

**Name:** `outbound_route_health_audit.py`  
**Purpose:** Scan all sites (or CSV subset) for Default Outbound Route presence, enabled state, and trunk assignment.

**Problem it solves:** CLEMKT lesson — route "present" ≠ dialable. `org_cleanup_audit.py` flags routes on sites with zero phones but does not verify trunk/enabled.

**Proposed implementation:**

| Aspect | Detail |
|--------|--------|
| Surface | CLI + Ops Demo (read-only) |
| Key APIs | `GET /api/v2/telephony/providers/edges/sites`, `GET .../sites/{id}/outboundroutes`, `GET .../outboundroutes/{id}` |
| Outputs | CSV/XLSX: site, route name, enabled, trunk names, phone count, status (OK / MISSING / DISABLED / NO_TRUNK) |

**Risk:** **Low** — read-only.  
**Guardrails:** None beyond rate limiting.

**Priority:** **P1** (run monthly; include in market site setup post-check)

---

### 3. Extension Duplicate Audit & Resolver

**Name:** `extension_duplicate_audit.py` + `extension_duplicate_resolve.py`  
**Purpose:** Find WORK extensions assigned to multiple users; assign next available pool extension and update Primary Voice.

**Problem it solves:** CJ Walker duplicate x15152 (2026-09-04). `_fix_cj_walker_ext.py` is name-needle hardcoded. `extensions_by_title.py` assigns but does not detect conflicts.

**Proposed implementation:**

| Aspect | Detail |
|--------|--------|
| Surface | CLI; Ops Demo for audit (read-only) + resolve (mutating, single-user or CSV) |
| Inputs | Optional `--extension`, `--user-email`, `--pool 15100-15999\|8300-8500` |
| Key APIs | `GET /api/v2/users`, `PATCH /api/v2/users/{id}` (reuse `medical_assistant_telephony_align.build_patch`) |
| Outputs | Audit CSV: extension, user list, site, pool; Resolve CSV: old/new ext, conflict_with |

**Risk:** **Medium** — wrong user could get wrong extension.  
**Guardrails:** Dry-run default; resolve one user at a time unless CSV; show conflict holder before apply; never reassign without displaying both parties.

**Priority:** **P1**

---

### 4. User Queue + Telephony Provisioner

**Name:** `user_queue_telephony_provision.py`  
**Purpose:** Onboard users to a queue (joined=true/false), ensure Communicate - User role, create/attach WebRTC phone on site, assign WORK extension, set Primary Voice.

**Problem it solves:** OKC Mid-Del five-user setup (2026-09-04) needed `_provision_okc_mid_del.py` (748 lines, hardcoded names, queue candidates, phone base IDs). `_probe_okc_queues.py` debugged membership API quirks.

**Proposed implementation:**

| Aspect | Detail |
|--------|--------|
| Surface | **Ops Demo UI** (primary) + CLI |
| Inputs | CSV: name or email, queue name, joined (true/false), site code, optional extension; or multi-user form |
| Key APIs | Users search, `POST /api/v2/authorization/roles`, queue members POST/PATCH, `POST .../phones`, user PATCH, station association check |
| Outputs | Per-user status CSV: roles_added, queue_status, phone_created/exists, extension, primary_voice, verify_pass |
| Phases | 1) Audit 2) Apply 3) Verify (re-query queue members + phone site + extension) |

**Risk:** **High** — creates phones, changes roles/queues.  
**Guardrails:** Dry-run audit phase required; WebRTC create uses org-standard phone base settings from config (not hardcoded UUIDs); skip users already compliant; `--apply` limited to CSV rows marked `approved=yes`.

**Priority:** **P1**

---

### 5. Telephony Profile Compliance Audit (title/queue scoped)

**Name:** `telephony_profile_compliance_audit.py`  
**Purpose:** Report users missing WORK extension, wrong Primary Voice (Teams vs WORK), phone site ≠ queue site, missing WebRTC phone/station.

**Problem it solves:** Genesis Ibarra (PHX Mesa) — infrastructure present but Primary Voice on Teams OTHER, no WORK ext; peer comparison revealed gap. Multiple `_probe_*` user scripts.

**Proposed implementation:**

| Aspect | Detail |
|--------|--------|
| Surface | CLI + Ops Demo |
| Inputs | `--title-needle`, `--queue-name`, `--compare-to-queue-peers` |
| Key APIs | Users, phones, stations, queue members, user detail addresses/primaryContactInfo |
| Outputs | CSV with issue codes: `MISSING_WORK_EXT`, `PRIMARY_NOT_WORK`, `PHONE_SITE_MISMATCH`, `NO_WEBRTC`, `STATION_NOT_ASSOCIATED` |
| Fix path | Link rows to `medical_assistant_telephony_align.py` or provisioner |

**Risk:** **Low** (audit).  
**Guardrails:** Read-only audit; separate apply tool.

**Priority:** **P1**

---

### 6. Medical Assistant Telephony Align (promote + policy)

**Name:** Promote existing `medical_assistant_telephony_align.py`  
**Purpose:** Standardize MA telephony: WORK extension, Primary Voice = WORK, preserve Teams OTHER.

**Problem it solves:** Genesis Ibarra fix pattern; earlier `oneoff_ma_primary_voice_other.py` ran **opposite** policy — need one canonical MA telephony policy in Ops Demo.

**Proposed implementation:**

| Aspect | Detail |
|--------|--------|
| Surface | Register in `app/tool_specs.py` + deprecate/remove `oneoff_ma_primary_voice_other.py` or gate behind explicit `--policy teams-primary` |
| Inputs | `--title-needle`, `--user-email`, `--limit N` for pilot |
| Outputs | Existing CSV format; add rollback column compatible with revert script |

**Risk:** **Medium** — bulk PATCH; `_revert_ma_ext_reassign.py` proves bad runs happen.  
**Guardrails:** Dry-run default; `--max-apply N`; always write timestamped CSV before apply; built-in `--revert-from-csv` subcommand.

**Priority:** **P1** (promotion, not rewrite)

---

### 7. Sales HQ Phone Workflow (audit → HR → apply)

**Name:** Wire existing trio as one Ops Demo workflow  
**Purpose:** Find Sales queue members on HQ Web phones; export HR template; apply reviewed assignments.

**Problem it solves:** Post-CLEMKT global Sales audit (2026-09-04) — 16 CLE phones moved, other markets still on HQ. Tools exist but disconnected from UI.

**Scripts to register:**

1. `sales_hq_phone_audit.py` — read-only
2. `export_hq_phone_hr_template.py` — read-only export
3. `apply_hq_phone_site_assignments.py` — mutating
4. (Optional) `reassign_hq_phones_by_active_queue.py` — auto for unambiguous joined=true cases

**Risk:** **Medium** — wrong site affects e911/caller ID.  
**Guardrails:** HR CSV required for apply; validate site codes against org; dry-run shows current vs target site; never auto-apply ambiguous multi-queue users (already in reassign tool).

**Priority:** **P2**

---

### 8. Site Caller ID from Location ELIN (promote)

**Name:** Promote `set_site_caller_id_from_location.py`  
**Purpose:** Bulk-sync site outbound caller ID from location emergency number (ELIN).

**Problem it solves:** Caller ID chain issues for HQ vs market sites (transcript: direct-dial outbound shows org default when site caller ID unset). Complements ELIN patch tool already in Ops Demo.

**Proposed implementation:** Register in `tool_specs.py`; optional `--site-code` filter; skip list (Papago) already in script.

**Risk:** **Medium** — mis-set caller ID affects outbound identity.  
**Guardrails:** Dry-run CSV preview; skip sites without ELIN; never overwrite manually overridden sites without flag.

**Priority:** **P2**

---

### 9. WebRTC Phone Creator

**Name:** `create_webrtc_phone.py`  
**Purpose:** Create `{Name} Web` phone on specified site for user, using org phone base settings.

**Problem it solves:** `_test_phone_create.py` and inline code in `_provision_okc_mid_del.py` duplicate phone POST payloads with hardcoded base/line template IDs.

**Proposed implementation:**

| Aspect | Detail |
|--------|--------|
| Surface | CLI + Ops Demo (single user form) |
| Inputs | User email, site code; load defaults from `config/telephony_defaults.json` |
| Key APIs | `POST /api/v2/telephony/providers/edges/phones`, station association check |
| Outputs | phone_id, site, webRtcUser link status |

**Risk:** **Medium**.  
**Guardrails:** Refuse if user already has Web phone; dry-run shows payload; config file for base settings (no hardcoded UUIDs in script).

**Priority:** **P2**

---

### 10. Create New Center (complete apply path)

**Name:** Extend `create_new_center.py`  
**Purpose:** Move from preflight JSONL planner to full apply: location, site, routes, queue names, back-office contact.

**Problem it solves:** Registry advertises center creation; apply mode explicitly warns "preflight-only until endpoint hardening." Real work uses `_setup_clemkt.py` instead.

**Proposed implementation:** Multi-step apply using market site wizard (#1) + queue creation stubs; or delegate to #1 per CSV row.

**Risk:** **High**.  
**Guardrails:** Phase rollout — preflight → apply location/site only → full.

**Priority:** **P2**

---

### 11. Migrate registry stubs off `generic_tool.py`

**Name:** Implement or redirect stubs  
**Purpose:** Stop Ops Demo from launching placeholder exit code 2.

| Stub ID | Recommendation |
|---------|----------------|
| `create_sites_locations` | Redirect to `create_sites_from_csv.py` or remove duplicate entry |
| `assign_phones_sites` | New tool: CSV user email + site code → move Web phone |
| `division_manager` | Port from archived `genesys_division_manager.py` or drop |
| `location_code_division` | Port or document as HR offline process |
| `queue_verify` | New read-only tool: queue flow, members, wrap-up codes vs template CSV |

**Priority:** **P2** (assign_phones_sites, queue_verify); **P3** (division stubs)

---

### 12. Station & Other-Phone Compliance Audits (promote)

**Name:** Register `station_name_audit.py`, `other_phone_site_mismatch_audit.py`  
**Purpose:** Catch station naming drift and Teams OTHER numbers whose area code doesn't match site market.

**Problem it solves:** Telephony hygiene after bulk moves; supports area_code_markets.csv config already in repo.

**Risk:** **Low**.  
**Priority:** **P2**

---

### 13. MA Provisioning Gap Audit (promote)

**Name:** Register `medical_assistant_provisioning_audit.py`  
**Purpose:** Active MAs missing Communicate - User and/or any phone assignment.

**Problem it solves:** Recurring gap lists before Genesis-style incidents; complements #5.

**Risk:** **Low**.  
**Priority:** **P2**

---

### 14. Org Cleanup Audit (promote + schedule)

**Name:** Register `org_cleanup_audit.py`; optional monthly job  
**Purpose:** Orphan flows, empty groups, routes on phone-less sites, unused wrap-up codes.

**Problem it solves:** First run 2026-08-07 — 90 findings; CLI-only today.

**Risk:** **Low** (read-only).  
**Priority:** **P3**

---

### 15. Primary Voice Policy Manager

**Name:** `primary_voice_policy_apply.py`  
**Purpose:** Title/role-based Primary Voice rules (WORK vs Teams OTHER) with explicit policy profiles.

**Problem it solves:** Conflicting one-offs — `oneoff_ma_primary_voice_other.py` (Teams primary) vs `medical_assistant_telephony_align.py` (WORK primary). Different cohorts/times needed different policies without a single switch.

**Proposed implementation:** Policy YAML: `{title_needle, primary: work|other, require_work_ext: bool}`; audit + apply modes.

**Risk:** **Medium**.  
**Guardrails:** One active policy per title class; dry-run diff; no silent bulk flip without `--policy-name`.

**Priority:** **P2**

---

### 16. Queue Membership Verifier

**Name:** `queue_membership_verify.py`  
**Purpose:** Given user(s) + queue, confirm membership and joined flag via all API paths (user/queues, queue/members filter, paged scan).

**Problem it solves:** `_probe_okc_queues.py` — OKC provisioning needed three API methods to confirm joined=false stuck correctly.

**Risk:** **Low**.  
**Priority:** **P3** (diagnostic, but cheap to build from probe code)

---

### 17. Align Run Rollback

**Name:** Subcommand on MA align or standalone `telephony_align_rollback.py`  
**Purpose:** Revert extensions from a prior align CSV (`_revert_ma_ext_reassign.py` pattern).

**Risk:** **Medium**.  
**Guardrails:** Only revert rows where `work_extension_before != work_extension_after` and action contained reassign; require `--apply`.

**Priority:** **P2**

---

## Ops Demo registration backlog

Wire these existing scripts into `app/tool_specs.py` + `app/tool_help.py` (no logic rewrite needed):

1. `set_site_caller_id_from_location.py`
2. `medical_assistant_telephony_align.py`
3. `medical_assistant_provisioning_audit.py`
4. `sales_hq_phone_audit.py`
5. `export_hq_phone_hr_template.py`
6. `apply_hq_phone_site_assignments.py`
7. `reassign_hq_phones_by_active_queue.py`
8. `station_name_audit.py`
9. `other_phone_site_mismatch_audit.py`
10. `org_cleanup_audit.py`

---

## Suggested implementation order

| Phase | Deliverables | Target |
|-------|--------------|--------|
| **Phase 1** | #2 Outbound route audit, #1 Market site wizard (with verification), register ELIN caller ID tool | Next market opening |
| **Phase 2** | #3 Extension duplicates, #4 User provisioner, #5 Telephony compliance audit, promote MA align (#6) | Onboarding / MA hygiene |
| **Phase 3** | #7 Sales HQ workflow UI, #9 WebRTC creator, #11 stub migration, #15 Primary voice policy | Reduce one-offs |
| **Phase 4** | #10 Create new center apply, #14 scheduled org audit, #16 queue verifier | Lifecycle maturity |

---

## One-off scripts to retire after canonical tools ship

| Retire | Replace with |
|--------|--------------|
| `_setup_clemkt.py` | `market_site_setup.py` |
| `_probe_clemkt_template.py` | `--dump-template` on #1 |
| `_provision_okc_mid_del.py` | `user_queue_telephony_provision.py` |
| `_fix_cj_walker_ext.py`, `_verify_cj_ext.py` | `extension_duplicate_resolve.py` |
| `_revert_ma_ext_reassign.py` | `--revert-from-csv` on MA align |
| `_test_phone_create.py` | `create_webrtc_phone.py` |
| `_probe_okc_queues.py` | `queue_membership_verify.py` |
| `_probe_genesis_ibarra.py`, `_probe_brynne_*`, `_probe_cj_context.py`, `_probe_charity.py` | `telephony_profile_compliance_audit.py` |
| `oneoff_ma_primary_voice_other.py` | `primary_voice_policy_apply.py` (explicit policy) |

Keep `_probe_*` only when actively debugging new API behavior; move reusable probes into `tools/_dev/` or delete after promotion.

---

## Configuration recommendations

Add `config/telephony_defaults.json` for org-wide constants currently hardcoded in one-offs:

```json
{
  "webrtc_phone_base_id": "...",
  "webrtc_line_template_id": "...",
  "default_outbound_trunk": "Pure-IP",
  "default_route_name": "Default Outbound Route",
  "extension_pools": {
    "high": [15100, 15999],
    "low": [8300, 8500]
  }
}
```

Reference from market site wizard, WebRTC creator, and provisioner.

---

## Summary table (priority)

| P | Tool | New vs promote |
|---|------|----------------|
| P1 | Market Site Setup Wizard | **New** (generalize `_setup_clemkt`) |
| P1 | Outbound Route Health Audit | **New** |
| P1 | Extension Duplicate Audit/Resolve | **New** |
| P1 | User Queue + Telephony Provisioner | **New** (generalize `_provision_okc_mid_del`) |
| P1 | Telephony Profile Compliance Audit | **New** |
| P1 | MA Telephony Align | **Promote** |
| P2 | Sales HQ Phone Workflow | **Promote** (3 scripts) |
| P2 | Site Caller ID from ELIN | **Promote** |
| P2 | WebRTC Phone Creator | **New** |
| P2 | Create New Center apply path | **Extend** |
| P2 | Registry stub migration | **Fix** |
| P2 | Station / Other-phone audits | **Promote** |
| P2 | MA Provisioning audit | **Promote** |
| P2 | Primary Voice Policy Manager | **New** |
| P2 | Align rollback | **New** (from revert script) |
| P3 | Org cleanup scheduled audit | **Promote** |
| P3 | Queue Membership Verifier | **New** |

---

*Generated from codebase inventory and agent transcript work patterns. Review with IT before enabling high-risk apply tools on production org.*

---

## Usage — Phase 1 tools (implemented 2026-09-09)

### Outbound Route Health Audit (`tools/telephony/outbound_route_health_audit.py`)

Read-only. Exit code 1 when any audited site fails route health checks.

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"

# All telephony sites
python tools/telephony/outbound_route_health_audit.py

# Subset from CSV (columns: site_code, site_name, or name)
python tools/telephony/outbound_route_health_audit.py --sites-csv outputs/site_filter.csv

# Include per-site phone counts (slower)
python tools/telephony/outbound_route_health_audit.py --include-phone-count
```

Output: `outputs/outbound_route_health_audit_YYYYMMDD_HHMMSS.csv`  
Status values: `OK`, `MISSING`, `DISABLED`, `NO_TRUNK`, `ERROR`

Also registered in Ops Demo as **Outbound Route Health Audit** (Telephony).

---

### Market Site Setup (`tools/telephony/market_site_setup.py`)

Dry run by default. Use `--apply` to create infrastructure. **Post-apply verification fails the run** if Default Outbound Route is missing, disabled, or has no external trunk.

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"

# Dry run — preview location, site, route payloads
python tools/telephony/market_site_setup.py `
  --site-code CLEMKT `
  --location-name Cleveland `
  --caller-id +12167699935 `
  --copy-from-location CLEJEF `
  --copy-from-site CLEJEF `
  --queue-name "Sales - CLE - Cleveland"

# Apply
python tools/telephony/market_site_setup.py `
  --site-code CLEMKT `
  --location-name Cleveland `
  --caller-id +12167699935 `
  --copy-from-location CLEJEF `
  --apply

# Export live site config as template reference (read-only)
python tools/telephony/market_site_setup.py --dump-template CLEMKT

# Phone moves from reviewed CSV (phone_id or user_email; optional target_site_code)
python tools/telephony/market_site_setup.py `
  --site-code CLEMKT --location-name Cleveland --caller-id +12167699935 `
  --copy-from-location CLEJEF `
  --phone-moves-csv outputs/hq_phone_hr_assignment_template_REVIEWED.csv `
  --apply
```

Output: `outputs/market_site_setup_{sitecode}_YYYYMMDD_HHMMSS.csv`

Required: `--site-code` (6 chars), `--location-name`, `--caller-id` (E.164).  
Address: `--copy-from-location` **or** `--address-line1`, `--city`, `--state`, `--zip`.  
Optional: `--trunk-name` (default Pure-IP), `--queue-name`, `--phone-moves-csv`, `--hq-site` (default Headquarters).

Also registered in Ops Demo as **Market Site Setup** (Telephony). Use dry run in the UI before enabling Apply.

**Recommended workflow:** run outbound route health audit before and after market site setup to confirm the new site shows `OK` and no regressions elsewhere.
