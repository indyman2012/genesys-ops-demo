# GenesysCloudToolkit — Ops Demo & Production Migration Recommendations

**Date:** 2026-09-09  
**Project:** `C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit`  
**Audience:** Doug + IT/Azure handoff owners  
**Context:** Recent work (git untracked hints): MA telephony align, Sales HQ phone audit, CLEMKT market setup, queue staffing gap reports, weekly email automation, HQ phone HR workflow.

---

## Executive summary

The toolkit has **~27 tools already wired into Ops Demo** (`app/tool_specs.py` → streamed CLI runner), plus **~20 newer CLI-only scripts** (audits, telephony workflows, reporting libraries) and **~17 scheduled jobs** (mostly weekly email reports on Doug's PC). The Flask app also ships **Queue Performance**, **Not Responding**, **Site Picker**, and an **Agent Workspace** pilot.

**Highest-priority migrations:**

1. **Tier 1 → Ops Demo:** Read-only telephony/MA audits and the HQ-phone reassignment workflow (audit → HR template → apply). These are repeatable, dry-run by default, and directly support ongoing ops work.
2. **Already production-ready → Azure scheduler:** The eight weekly/biweekly/daily report jobs with Gmail send wrappers — move scheduling from Windows Task Scheduler to Azure once O365 or a service mailbox replaces personal Gmail.
3. **Keep CLI-only:** Root `_probe_*` / `_fix_*` one-offs, destructive bulk deletes, org-wide ELIN patch, and superseded one-off mutators.

**Do not migrate one-offs without generalizing first** (e.g. `_setup_clemkt.py`, `_provision_okc_mid_del.py`) — extract patterns into reusable tools already partially present (`create_sites_from_csv`, `create_outbound_routes`, `update_outbound_routes`).

---

## Current Ops Demo surface (already implemented)

| Feature | Location | Maturity | Risk | Notes |
|---------|----------|----------|------|-------|
| Tool catalog UI (27 tools) | `app/tool_specs.py`, `app/ops_routes.py` | Reusable | Mixed | Demo mode forces dry-run; `EXECUTE` confirm for mutators |
| Queue Performance dashboard | `app/queue_performance.py`, `/queue-performance` | Production-grade | Read-only | OAuth-gated; PDF/XLSX export wired |
| Not Responding agents | `app/not_responding.py` | Reusable | Read-only | Live API poll |
| Site Picker (agent self-service) | `app/site_picker/` | Pilot | Mutating (agent token) | Separate OAuth; phone site change |
| Agent Workspace | `app/agent_workspace/` | Demo / pilot | Mixed | Live queue panels + demo AHT report |
| Tool runner | `app/tool_runner.py` | Reusable | — | Streams stdout; uploads CSV templates |

**Demo exclusions:** `delete_sites_locations` hidden in demo mode (`core/demo_mode.py`).

---

## Comprehensive inventory

Legend:

- **Maturity:** `Reusable` = parameterized CLI with dry-run; `Workflow` = multi-step CSV chain; `One-off` = hardcoded IDs/names; `Library` = imported by other scripts; `Legacy` = old SDK pattern
- **Risk:** `Read` = audit/export only; `Low` = scoped single-user/single-site mutator with dry-run; `Med` = bulk CSV mutator; `High` = org-wide or destructive
- **Usage:** `Ops Demo` = already in UI; `CLI` = manual only; `Scheduled` = Windows task / job wrapper; `App` = embedded in Flask routes

### A. `tools/audits/` (9 scripts)

| Script | Purpose | Maturity | Deps | Risk | Usage | In Ops Demo? |
|--------|---------|----------|------|------|-------|--------------|
| `permission_audit.py` | Export users, roles, permissions | Reusable | `core.cli`, Genesys Auth API | Read | Ops Demo | Yes |
| `callback_sla_audit.py` | Architect callback SLA paired-call audit | Reusable | Analytics API, date range | Read | Ops Demo | Yes |
| `validate_reviewed_user_site_data.py` | Validate HR-reviewed user/site CSV vs Genesys | Reusable | CSV input | Read | Ops Demo | Yes |
| `cleanup_attachment_audit.py` | Audit extra sites/locations vs keep-list | Reusable | CSV keep-list | Read | Ops Demo | Yes |
| `org_cleanup_audit.py` | Org-wide orphan audit (queues, sites, routes, flows) | Reusable | openpyxl | Read | CLI | **No** |
| `medical_assistant_provisioning_audit.py` | MA users missing Communicate role and/or phone | Reusable | `core.cli` | Read | CLI (recent) | **No** |
| `sales_hq_phone_audit.py` | Sales queue members on HQ Web phones vs market sites | Reusable | Shares logic with HQ reassign | Read | CLI (recent) | **No** |
| `station_name_audit.py` | Station name vs `{Name} Web` convention | Reusable | Stations + Users API | Read | CLI | **No** |
| `other_phone_site_mismatch_audit.py` | Other Phone area code vs WEB site market code | Reusable | openpyxl, area_code config | Read | CLI | **No** |

### B. `tools/reporting/` (14 scripts + 1 library)

| Script | Purpose | Maturity | Deps | Risk | Usage | In Ops Demo? |
|--------|---------|----------|------|------|-------|--------------|
| `staffing_metrics.py` | Shared FTE/burden fetchers for analytics | Library | Genesys Analytics, `core.erlang` | Read | Imported | N/A |
| `queue_staffing_gap_report.py` | Weekly PS staffing gap (Erlang C vs on-queue FTE) | Reusable | staffing_metrics, config CSVs | Read | Scheduled | **No** |
| `direct_outbound_agents_report.py` | Agents with high direct (non-queue) outbound volume | Reusable | Analytics | Read | Scheduled + CLI | **No** |
| `spam_short_abandon_weekly_report.py` | Weekly inbound short-abandon + Spam Call by queue | Reusable | Analytics | Read | Scheduled | **No** |
| `spam_short_abandon_report.py` | Org-wide abandon/spam (daily/weekly/monthly) | Reusable | Analytics | Read | CLI | **No** |
| `wrapup_timeout_weekly_report.py` | Weekly ININ-WRAP-UP-TIMEOUT by agent | Reusable | Analytics | Read | Scheduled | **No** |
| `repeat_caller_weekly_report.py` | Weekly repeat inbound callers by queue | Reusable | Analytics | Read | Scheduled | **No** |
| `callback_conversion_weekly_report.py` | Weekly callback conversion by queue | Reusable | Analytics | Read | Scheduled | **No** |
| `aht_trend_biweekly_report.py` | 4-week inbound voice AHT HTML trend | Reusable | Analytics, HTML charts | Read | Scheduled | **No** |
| `queue_performance_pdf_report.py` | Queue Performance PDF (overview/detail) | Reusable | Queue perf logic | Read | App export + Scheduled | Partial (via UI) |
| `care_navigator_multi_queue_report.py` | Care Navigators joined on >1 queue | Reusable | openpyxl | Read | Scheduled (daily) | **No** |
| `ps_queue_wrapup_report.py` | PS market wrap-up code counts (rolling window) | Reusable | Analytics | Read | CLI | **No** |
| `wrapup_code_report.py` | 3-month wrap-up usage by center/queue/month | Reusable | Analytics | Read | CLI | **No** |
| `run_local_reports.py` | MySQL cache rollups (membership, phones, users) | Reusable | SQLAlchemy, local DB | Read | CLI (DB env) | **No** |

### C. `tools/telephony/` (14 scripts)

| Script | Purpose | Maturity | Deps | Risk | Usage | In Ops Demo? |
|--------|---------|----------|------|------|-------|--------------|
| `audit_sites_locations_pre_delete.py` | Pre-delete dependency audit | Reusable | CSV keep-list | Read | Ops Demo | Yes |
| `create_sites_from_csv.py` | Create missing sites linked to locations | Reusable | CSV | Med | Ops Demo | Yes |
| `create_outbound_routes.py` | Create default outbound routes per site | Reusable | CSV | Med | Ops Demo | Yes |
| `update_outbound_routes.py` | Enable/configure existing outbound routes | Reusable | CSV | Med | Ops Demo | Yes |
| `bulk_update_sites.py` | Bulk site caller ID / auto-update | Reusable | CSV | Med | Ops Demo | Yes |
| `reassign_phones_to_headquarters.py` | Move phones from delete-candidate sites to HQ | Reusable | Audit CSV | Med | Ops Demo | Yes |
| `delete_sites_locations_full_pass.py` | DELETE sites and locations from audit CSVs | Reusable | Audit CSV | **High** | Ops Demo (demo-excluded) | Yes |
| `patch_location_elin.py` | Set ELIN on all locations with emergency numbers | Reusable | — | **High** (org-wide) | Ops Demo | Yes |
| `set_site_caller_id_from_location.py` | Set site callerId from location ELIN E.164 | Reusable | dry-run default | Med | CLI (CLEMKT pattern) | **No** |
| `reassign_hq_phones_by_active_queue.py` | HQ WebRTC → center site via joined queue map | Workflow | dry-run default | Med | CLI (recent) | **No** |
| `export_hq_phone_hr_template.py` | HR template CSV from HQ reassign audit | Workflow | Prior audit CSV | Read | CLI | **No** |
| `apply_hq_phone_site_assignments.py` | Apply HR-reviewed site codes to HQ phones | Workflow | Reviewed CSV | Med | CLI | **No** |

### D. `tools/queues/` (13 scripts)

| Script | Purpose | Maturity | Deps | Risk | Usage | In Ops Demo? |
|--------|---------|----------|------|------|-------|--------------|
| `provisioning_snapshot.py` | User provisioning CSV export | Reusable | Division filter | Read | Ops Demo | Yes |
| `queue_members_export.py` | Export queue members | Reusable | CSV/queue name | Read | Ops Demo | Yes |
| `queue_joined_true.py` / `queue_joined_false.py` | Activate/deactivate user on queue prefix | Reusable | CSV, dry-run | Low | Ops Demo | Yes |
| `queue_remove_user.py` | Remove user from queues by prefix | Reusable | dry-run | Low | Ops Demo | Yes |
| `remove_user_from_all_queues.py` | Remove user from every queue | Reusable | dry-run | Med | Ops Demo | Yes |
| `external_contacts.py` | Create missing external contacts from CSV | Reusable | CSV | Low | Ops Demo | Yes |
| `extensions_by_title.py` | Assign extensions from pool by title | Reusable | CSV | Med | Ops Demo | Yes |
| `assign_group_to_patient_queue_highest_ring.py` | Add group to Patient* highest ring | Reusable | Group picker | Med | Ops Demo | Yes |
| `wrapupcodes_apply.py` | Create/assign wrap-up codes to NCM queues | Reusable | CSV, `--execute` | Med | Ops Demo | Yes |
| `create_new_center.py` | New center preflight planner | Reusable | CSV | Low (plan only) | Ops Demo | Yes |
| `generic_tool.py` | Placeholder for unmigrated registry tools | Stub | — | — | Unused | No |

### E. `tools/users_roles/` (10 scripts)

| Script | Purpose | Maturity | Deps | Risk | Usage | In Ops Demo? |
|--------|---------|----------|------|------|-------|--------------|
| `license_counts.py` | Aggregate license counts | Reusable | License API | Read | Ops Demo | Yes |
| `role_assign.py` | Add/remove division grants on user roles | Reusable | CSV, dry-run inverted | Med | Ops Demo | Yes |
| `cx_license_review.py` | CX1 vs CX2 permission review for one user | Reusable | — | Read | Ops Demo | Yes |
| `create_roaming_agent_role.py` | Create/update Site Picker roaming role | Reusable | Site list | Med | Ops Demo | Yes |
| `create_cx1_test_role.py` | Build CX1 test role from review JSON | Reusable | JSON file | Med | Ops Demo | Yes |
| `add_generated_emails.py` | Generate emails from names (offline) | Reusable | CSV/XLSX | Read | Ops Demo | Yes |
| `medical_assistant_telephony_align.py` | MA WORK ext + Primary Voice align | Reusable | dry-run default | Med | CLI (recent) | **No** |
| `oneoff_ma_primary_voice_other.py` | One-off: Primary Voice → Other for MAs | One-off | Superseded | Med | CLI | **No** |
| `role_division_tool.py` | Legacy SDK role division assignment | Legacy | PureCloud SDK, client.env | Med | CLI | **No** |

### F. `tools/email/`

| Script | Purpose | Maturity | Deps | Risk | Usage |
|--------|---------|----------|------|------|-------|
| `gmail_send.py` | Send email via personal Gmail (SMTP/OAuth) | Reusable | `doug.env`, optional Gmail API | Low | All scheduled jobs |

### G. `jobs/` (17 scripts)

| Job | Wraps | Schedule (PC) | Email? | Azure candidate? |
|-----|-------|---------------|--------|------------------|
| `weekly_direct_outbound_report.py` | `direct_outbound_agents_report.py` | Mon 5 AM, 5-run retire | Gmail | **Yes** |
| `weekly_spam_abandon_report.py` | `spam_short_abandon_weekly_report.py` | Mon 6 AM, 8-run retire | Gmail | **Yes** |
| `weekly_wrapup_timeout_report.py` | `wrapup_timeout_weekly_report.py` | Mon 7 AM, 8-run retire | Gmail | **Yes** |
| `weekly_repeat_caller_report.py` | `repeat_caller_weekly_report.py` | Mon (registered) | Gmail | **Yes** |
| `weekly_callback_conversion_report.py` | `callback_conversion_weekly_report.py` | Mon (registered) | Gmail | **Yes** |
| `weekly_queue_staffing_gap_report.py` | `queue_staffing_gap_report.py` | Mon (registered) | Gmail | **Yes** |
| `biweekly_aht_trend_report.py` | `aht_trend_biweekly_report.py` | Biweekly (registered) | Gmail | **Yes** |
| `daily_care_navigator_multi_queue_report.py` | `care_navigator_multi_queue_report.py` | Daily (registered) | Gmail | **Yes** |
| `weekly_queue_performance_pdf.py` | `queue_performance_pdf_report.py` | Weekly (registered) | No (file copy) | **Yes** |
| `sync_genesys_users_cache.py` | Users → MySQL `gc_users` | Manual | — | **Yes** (needs Azure MySQL) |
| `sync_genesys_queues_cache.py` | Queues → MySQL | Manual | — | **Yes** |
| `build_queue_daily_metrics.py` | Analytics rollups | Manual | — | **Yes** |
| `build_queue_daily_metrics_sqlalchemy.py` | SQLAlchemy variant | Manual | — | **Yes** |
| `build_agent_daily_metrics_sqlalchemy.py` | Agent metrics | Manual | — | **Yes** |
| `collect_reporting_data.py` | Legacy collector | Manual | — | Maybe (legacy) |
| `cleanup_mysql_retention.py` | DB retention | Manual | — | **Yes** |
| `backfill_queue_daily_metrics_divisions.py` | One-time backfill | One-off | — | No |

### H. Root `_*.py` scripts (30 files)

| Script | Purpose | Maturity | Risk | Recommendation |
|--------|---------|----------|------|----------------|
| `_setup_clemkt.py` | Cleveland location + CLEMKT site + phone moves + route verify | One-off | Med | **Do not migrate** — generalize into site setup checklist tool |
| `_provision_okc_mid_del.py` | OKC Mid-Del PS user provisioning (role, queue, phone, ext) | One-off | Med | **Do not migrate** — extract as parameterized provision wizard later |
| `_probe_clemkt_template.py` | Read-only CLEMKT site config for docs | One-off | Read | Archive / delete after template captured |
| `_probe_staffing_metrics.py` | Probe FTE/presence metrics API | One-off | Read | Fold learnings into staffing report; delete |
| `_probe_ai_opportunities.py` | Queue/flow/wrap-up inventory for AI analysis | One-off | Read | Keep CLI-only until AI project resumes |
| `_probe_presence_batch.py` | User details batch presence probe | One-off | Read | Delete after API behavior confirmed |
| `_probe_presence_per_user.py` | Per-user presence probe | One-off | Read | Delete |
| `_probe_brynne_*.py` (4 files) | Brynne Ramella telephony investigation | One-off | Read | Delete |
| `_probe_genesis_ibarra.py` | Genesis Ibarra outbound config | One-off | Read | Delete |
| `_probe_okc_queues.py` / `_probe_okc_phone.py` | OKC queue/phone debugging | One-off | Read | Delete after OKC provision complete |
| `_probe_charity.py` / `_probe_cj_context.py` | User telephony debugging | One-off | Read | Delete |
| `_probe_cleelk_full.py` | Site outbound field probe | One-off | Read | Delete |
| `_probe_toast_test.py` | Native Genesys toast POC | One-off | Med | Keep CLI — future Queue Alert Monitor dependency |
| `_fix_cj_walker_ext.py` | Fix duplicate WORK extension | One-off | Low | Delete after applied |
| `_revert_ma_ext_reassign.py` | Revert wrong MA extension assignments | One-off | Med | Delete after applied |
| `_verify_cj_ext.py` | Verify extension holders | One-off | Read | Delete |
| `_test_phone_create.py` | Hardcoded phone CREATE test | One-off | **High** | **Never migrate** |
| `_sdk_probe.py` | List SDK class names | One-off | Read | Delete |
| `_doug_queues.py` | Doug Hendrickson queue membership | One-off | Read | Personal; keep CLI |
| `_generate_azure_hosting_doc.py` | Azure hosting Word doc generator | One-off | Read | IT handoff utility; CLI-only |

### I. `core/` utilities (12 modules)

| Module | Purpose | Migrate? |
|--------|---------|----------|
| `genesys_client.py` | HTTP client, paging, error handling | Shared infrastructure — already used everywhere |
| `cli.py` | `create_client`, dry-run helpers, CSV I/O | Shared infrastructure |
| `config.py` | Credential probing, env resolution | Shared infrastructure |
| `paths.py` | outputs/logs/templates paths | Shared infrastructure |
| `demo_mode.py` | Ops Demo dry-run enforcement | App-only |
| `lookups.py` | Queue/user lookup helpers | Shared library |
| `erlang.py` | Erlang C staffing math | Used by staffing reports |
| `db.py` | MySQL/SQLAlchemy engine | Azure production DB layer |
| `genesys_api_cache_helpers.py` | Cache helpers | Azure metrics pipeline |
| `genesys_pydantic_models.py` | Typed models | Optional validation layer |
| `pydantic_validation_helpers.py` | Validation utilities | Optional |
| `verify_auth.py` | Auth smoke test | Dev/ops utility |

### J. `app/` web features (beyond tool catalog)

| Module | Purpose | Ops Demo / Production path |
|--------|---------|----------------------------|
| `queue_performance.py` + exports | Live queue metrics dashboard | **Production** — already primary Ops Demo value |
| `not_responding.py` | Agents not responding poll | **Tier 1** — keep prominent in Ops home |
| `site_picker/` | Agent OAuth phone site change | **Production** (separate app entry) — not admin toolkit |
| `agent_workspace/` | Agent-facing queue panels + demo AHT | **Tier 2** — expand on Azure with agent OAuth |
| `ops_auth.py` | Queue Performance role gate | Production auth pattern |

### K. Design docs (not implemented)

| Doc | Status |
|-----|--------|
| `docs/queue_alert_monitor_design.md` | Design only — depends on toast POC (`_probe_toast_test.py`) |
| `docs/staffing_burden_report_design.md` | Phase 1 done (staffing gap report); Phase 2 burden TBD |
| `docs/member_facing_design_principles.md` | UX principles |
| `docs/agent_workspace_pilot.md` | Agent workspace scope |

---

## Tier 1 recommendations (do first)

High value, low risk, frequently needed. Estimated **total: ~2–3 weeks** if done incrementally.

| Item | Action | Rationale | Effort |
|------|--------|-----------|--------|
| **Medical Assistant Provisioning Audit** | Add to Ops Demo → **Audits › MA Provisioning** | Read-only; directly supports ongoing MA onboarding hygiene | **S** |
| **Sales HQ Phone Audit** | Add to Ops Demo → **Audits › Sales HQ Phones** | Read-only; complements HQ phone initiative | **S** |
| **Station Name Audit** | Add to Ops Demo → **Audits › Station Names** | Read-only; catches WebRTC naming drift | **S** |
| **Org Cleanup Audit** | Add to Ops Demo → **Audits › Org Cleanup** | Read-only; leadership-friendly XLSX | **S** |
| **Other Phone / Site Mismatch Audit** | Add to Ops Demo → **Audits › Phone Site Mismatch** | Read-only; uses `config/area_code_markets.csv` | **S** |
| **HQ Phone Reassign Workflow (3 tools)** | Add chain to Ops Demo → **Telephony › HQ Phone Workflow** | `reassign_hq_phones_by_active_queue` → `export_hq_phone_hr_template` → `apply_hq_phone_site_assignments`; all dry-run default | **M** |
| **Medical Assistant Telephony Align** | Add to Ops Demo → **Users/Roles › MA Telephony Align** | Reusable mutator with `--apply`; supersedes `oneoff_ma_primary_voice_other` | **M** |
| **Set Site Caller ID from Location** | Add to Ops Demo → **Telephony › Site Caller ID from ELIN** | Needed for every new market site (CLEMKT lesson); dry-run default | **S** |
| **Outbound Route Post-Create Verify** | Enhance `update_outbound_routes` UI help + optional verify step | CLEMKT lesson: route must have trunk **and** be enabled | **S** |
| **On-demand report hub (subset)** | New Ops Demo section **Reports › Run Report** | Care Navigator snapshot, Direct Outbound (custom dates), PS Wrap-up — download XLSX/CSV without email | **M** |

### Tier 1 UI grouping (proposed)

```
Ops Demo Home
├── Queue Performance          (existing — keep top nav)
├── Not Responding             (existing)
├── Reports                      ← NEW section
│   ├── Care Navigator Multi-Queue (on-demand)
│   ├── Direct Outbound Agents     (on-demand, date range)
│   ├── PS Wrap-up Codes           (on-demand)
│   └── [Scheduled reports: view last output / re-run]  ← stretch
├── Audits                       ← expand
│   ├── Permission Audit         (existing)
│   ├── MA Provisioning          (NEW)
│   ├── Sales HQ Phones          (NEW)
│   ├── Station Names            (NEW)
│   ├── Org Cleanup              (NEW)
│   ├── Phone Site Mismatch      (NEW)
│   └── …existing audits…
├── Telephony
│   ├── HQ Phone Workflow        ← NEW subgroup
│   │   ├── 1. Reassign by Active Queue (dry-run)
│   │   ├── 2. Export HR Template
│   │   └── 3. Apply HR Assignments
│   ├── Site Caller ID from ELIN (NEW)
│   └── …existing telephony tools…
└── Users / Roles
    ├── MA Telephony Align       (NEW)
    └── …existing…
```

---

## Tier 2 recommendations (next quarter)

Medium value or needs guardrail UX before wide exposure.

| Item | Action | Rationale | Effort |
|------|--------|-----------|--------|
| **Staffing Gap Report (on-demand)** | Ops Demo report runner + link to weekly output | Phase 1 implemented; leadership review artifact | **M** |
| **AHT Trend Report (on-demand)** | Ops Demo HTML preview / download | Already in Agent Workspace demo; unify | **M** |
| **Callback SLA Audit** | Already in UI — add saved presets for PS queues | Underused; date picker UX improvement | **S** |
| **Create New Center preflight** | Wizard UI: upload CSV → review plan → deep-link telephony steps | Reduces `_setup_*` one-offs | **L** |
| **Agent Workspace on Azure** | Production agent OAuth app | Site picker + live queues for agents | **L** |
| **Queue Alert Monitor** | Implement per design doc | High value; needs toast POC + scheduler | **L** |
| **MySQL-backed reports** | `run_local_reports.py` + cache sync jobs on Azure | Faster dashboards; decouples from live API | **L** |
| **Email from Azure** | Replace Gmail with O365/service mailbox | Required before moving scheduled jobs off Doug's PC | **M** |

---

## Tier 3 recommendations (Azure scheduled production)

Move off personal PC Task Scheduler once Azure App Service + secrets + mail are ready.

| Job | Priority | Notes |
|-----|----------|-------|
| Weekly Direct Outbound | P1 | Already registered; 5-run auto-retire pattern |
| Weekly Spam/Abandon | P1 | Mon 6 AM |
| Weekly Wrap-up Timeout | P1 | Mon 7 AM |
| Weekly Repeat Caller | P2 | |
| Weekly Callback Conversion | P2 | |
| Weekly Queue Staffing Gap | P1 | Leadership staffing artifact |
| Biweekly AHT Trend | P2 | HTML attachment |
| Daily Care Navigator Multi-Queue | P2 | |
| Weekly Queue Performance PDF | P2 | Shared folder copy — needs Azure storage path |
| `sync_genesys_*_cache` + metrics builders | P3 | After Azure MySQL provisioned |
| `cleanup_mysql_retention` | P3 | Monthly |

**Azure implementation pattern:** Azure WebJobs or Logic Apps timer → `python jobs/<job>.py` → store output in Blob/share → email via Graph API or SMTP relay.

**Ops Demo role:** Read-only "last run status + download" panel for scheduled reports (optional Tier 2).

---

## What should NOT move to Ops Demo

| Category | Examples | Why |
|----------|----------|-----|
| **Hardcoded one-off probes** | `_probe_*`, `_verify_*`, `_doug_queues.py` | Not parameterized; investigation debris |
| **Applied one-off fixes** | `_fix_cj_walker_ext.py`, `_revert_ma_ext_reassign.py` | Already executed; no repeat value |
| **Destructive tests** | `_test_phone_create.py` | Creates real phones with hardcoded IDs |
| **Org-wide bulk mutators without row-level guardrails** | `patch_location_elin.py` | Already in UI but should stay admin-only; consider removing from demo entirely |
| **Destructive delete pass** | `delete_sites_locations_full_pass.py` | Correctly demo-excluded; require IT-only access on production |
| **Superseded mutators** | `oneoff_ma_primary_voice_other.py`, `role_division_tool.py` | Use `medical_assistant_telephony_align` / `role_assign` instead |
| **One-shot market setups** | `_setup_clemkt.py`, `_provision_okc_mid_del.py` | Generalize first; patterns belong in CSV-driven tools |
| **Legacy DB collector** | `collect_reporting_data.py` | Superseded by sqlalchemy builders unless still needed |
| **Personal utilities** | `_generate_azure_hosting_doc.py`, `gmail_send.py` | Infrastructure / IT handoff, not operator UI |
| **Stub** | `generic_tool.py` | Remove or replace when tools migrated |

---

## Risk matrix (quick reference)

| Risk | Ops Demo suitable? | Examples |
|------|-------------------|----------|
| Read-only audit/export | **Yes — prioritize** | All `*_audit.py`, provisioning snapshot, reports |
| Scoped mutator + dry-run default | **Yes — with EXECUTE confirm** | Queue join/leave, MA align, HQ phone apply |
| CSV bulk mutator | **Yes — admin role only** | Sites, routes, extensions by title |
| Org-wide mutator | **Admin only; consider CLI-only** | `patch_location_elin` |
| Destructive delete | **Never in demo; IT break-glass** | `delete_sites_locations` |
| Hardcoded one-off | **Never** | `_test_phone_create`, `_setup_clemkt` |

---

## Implementation effort key

| Size | Meaning | Examples |
|------|---------|----------|
| **S** (~2–4 hours) | Add `tool_specs.py` entry + template + help text | Read-only audits, caller ID tool |
| **M** (~1–2 days) | Multi-field form, CSV chain, apply confirmation UX | HQ phone workflow, MA align, on-demand report runner |
| **L** (~3–10 days) | New UI section, OAuth, scheduler integration, or multi-step wizard | Agent Workspace production, Queue Alert Monitor, New Center wizard, Azure job port |

---

## Suggested migration sequence (90-day)

1. **Week 1–2:** Tier 1 read-only audits (5 scripts → `tool_specs.py`).
2. **Week 3:** HQ phone workflow trio + MA telephony align.
3. **Week 4:** Site caller ID tool + outbound route verify banner; on-demand report hub (Care Navigator + Direct Outbound).
4. **Month 2:** Azure email + port P1 weekly jobs; Ops Demo "scheduled reports" status page.
5. **Month 3:** Agent Workspace pilot on Azure; evaluate Queue Alert Monitor POC.

---

## Dependencies & config artifacts

Recent work introduced config CSVs that Ops Demo should surface as downloadable templates:

| File | Used by |
|------|---------|
| `config/area_code_markets.csv` | `other_phone_site_mismatch_audit.py` |
| `config/staffing_queue_defaults.csv` | `queue_staffing_gap_report.py` |
| `config/staffing_role_expectations.csv` | Staffing burden (Phase 2) |
| `templates_csv/*_template.csv` | Existing Ops Demo tools |

Ensure Azure deployment packages these under `config/` and `templates_csv/`.

---

## Summary counts

| Bucket | Count |
|--------|-------|
| Tools already in Ops Demo | 27 |
| Tools recommended for Tier 1 Ops Demo add | 10 |
| Reporting scripts (CLI/scheduled) | 14 |
| Scheduled jobs (PC → Azure candidates) | 8 email + 1 PDF + DB jobs |
| Root one-offs to retire | ~20 |
| Design-only features | 1 (Queue Alert Monitor) |

---

*Generated from codebase inventory on 2026-09-09. Align with monthly Azure handoff packaging when App Service migration completes.*

---

## Step 2 wired (2026-09-09)

Tier 1 audits and HQ phone workflow registered in `app/tool_specs.py` with matching help text in `app/tool_help.py`. All use the existing `app/tool_runner.py` CLI pattern (`build_command` → subprocess). Mutators use `apply_flag: apply` (dry-run unless EXECUTE confirmed; demo mode always dry-run).

| Tool ID | UI Name | Category | Script | Notes |
|---------|---------|----------|--------|-------|
| `ma_provisioning_audit` | MA Provisioning Audit | Audits | `tools/audits/medical_assistant_provisioning_audit.py` | Read-only |
| `sales_hq_phone_audit` | Sales HQ Phone Audit | Audits | `tools/audits/sales_hq_phone_audit.py` | Read-only |
| `station_name_audit` | Station Name Audit | Audits | `tools/audits/station_name_audit.py` | Read-only |
| `org_cleanup_audit` | Org Cleanup Audit | Audits | `tools/audits/org_cleanup_audit.py` | Read-only XLSX |
| `other_phone_site_mismatch_audit` | Phone Site Mismatch Audit | Audits | `tools/audits/other_phone_site_mismatch_audit.py` | Read-only; template `area_code_markets_template.csv` |
| `hq_phone_reassign_by_queue` | HQ Phone Reassign by Active Queue | Telephony | `tools/telephony/reassign_hq_phones_by_active_queue.py` | Workflow step 1; dry-run default |
| `export_hq_phone_hr_template` | Export HQ Phone HR Template | Telephony | `tools/telephony/export_hq_phone_hr_template.py` | Workflow step 2; requires step 1 audit CSV |
| `apply_hq_phone_site_assignments` | Apply HQ Phone Site Assignments | Telephony | `tools/telephony/apply_hq_phone_site_assignments.py` | Workflow step 3; dry-run default |
| `ma_telephony_align` | MA Telephony Align | Users / Roles | `tools/users_roles/medical_assistant_telephony_align.py` | Dry-run default |
| `set_site_caller_id_from_location` | Site Caller ID from ELIN | Telephony | `tools/telephony/set_site_caller_id_from_location.py` | Dry-run default |

**Catalog count:** 41 tools in `TOOL_SPECS` (was 31 before this step).

**Validation:** `build_command` verified for all 10 registrations. `export_hq_phone_hr_template` dry-run executed successfully against a test audit CSV. Org-wide audits (`org_cleanup_audit`, `sales_hq_phone_audit`, etc.) are expected to run 1–3 minutes live — not fully exercised in this pass.

**Not in scope (Step 2):** On-demand report hub (Care Navigator, Direct Outbound, PS wrap-up). Legacy `tool_registry.py` stubs still point at `generic_tool.py` — separate cleanup.

**Blockers:** None for registration. HQ workflow step 2/3 require prior CSV outputs from steps 1 / HR review (documented in tool help text).
