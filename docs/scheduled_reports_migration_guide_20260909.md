# Scheduled Reports & Migration Guide

**Project:** GenesysCloudToolkit  
**Document date:** 2026-09-09  
**Purpose:** Inventory all automated report jobs currently running on Doug's personal PC via Windows Task Scheduler, and define the migration path to work-hosted Azure App Service (or equivalent work infrastructure).

---

## 1. Executive summary

Today the GenesysCloudToolkit **web dashboard** runs on Render (demo) while **scheduled report jobs** run from Doug's personal Windows PC using Task Scheduler. Jobs generate Excel/HTML/PDF outputs, email most reports to `doug.hendrickson@archwellhealth.com` via personal Gmail SMTP (`doug.env`), and write logs/state under `logs/`.

After migration to work-hosted Azure App Service, these jobs become **work-owned production automation**. Doug continues Git-based development; IT applies **monthly deployable zip releases** with an operations doc (OAuth, startup, smoke tests).

**Target outcome:** All scheduled jobs run on work infrastructure with work-approved secrets and email delivery; personal PC Task Scheduler tasks are disabled and eventually removed.

---

## 2. Current run location

| Aspect | Current state |
|--------|---------------|
| **Host** | Doug's personal Windows PC |
| **Scheduler** | Windows Task Scheduler (`Register-ScheduledTask` via `scripts/register_*_task.ps1`) |
| **Project path** | `C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit` |
| **Python** | `python` on PATH (most jobs); business7d bat uses Python 3.13 Windows Store path |
| **Trigger behavior** | `-StartWhenAvailable` — if PC was asleep, job runs when PC wakes |
| **Principal** | Interactive user (`LogonType Interactive`) — PC must be logged in for reliable execution |
| **Auto-retire** | Several jobs delete their Task Scheduler entry via `schtasks /Delete` after N successful runs |

---

## 3. Shared dependencies (all jobs)

### 3.1 Genesys Cloud OAuth (API access)

| File / env | Purpose |
|------------|---------|
| `client.env` (or App Service env vars) | `GENESYS_CLIENT_ID`, `GENESYS_CLIENT_SECRET`, `GENESYS_REGION` |
| Grant type | **Client Credentials** (server-to-server) for all report tools |
| Optional | `GENESYS_AGENT_CLIENT_ID/SECRET` — authorization-code only; **not** used by scheduled jobs |

Verify locally:

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
python core/verify_auth.py
```

### 3.2 Email delivery (current: personal Gmail)

| File / env | Purpose |
|------------|---------|
| `doug.env` | `USER`, `PASSWORD` (Gmail app password), optional `FROM_NAME` |
| Script | `tools/email/gmail_send.py` |
| Default recipient | `doug.hendrickson@archwellhealth.com` |
| Extra pip (OAuth path only) | `pip install -r requirements-gmail.txt` |

**Migration note:** Personal Gmail SMTP works because O365 is blocked on Doug's personal PC. On a **work host**, prefer Archwell O365 SMTP relay, Microsoft Graph `sendMail`, or SharePoint/OneDrive drop + notification. Keep `gmail_send.py` as fallback during transition.

### 3.3 Python packages

```powershell
pip install -r requirements.txt
```

Core deps: `Flask`, `SQLAlchemy`, `PyMySQL`, `requests`, `purecloudplatformclientv2`, `openpyxl`, `reportlab`, `python-dotenv`, `pydantic`, `gunicorn`.

Email-only extras: `requirements-gmail.txt` (Google API client libs — only if using `--oauth`).

### 3.4 Runtime directories

| Path | Purpose |
|------|---------|
| `outputs/` | Generated reports (xlsx, html, pdf) |
| `logs/` | Per-run logs + JSON schedule state files |
| `config/staffing_queue_defaults.csv` | Queue staffing gap job |
| `config/staffing_role_expectations.csv` | Agent burden roles for staffing gap |

On Azure, mount **Azure Files** or use Blob storage for `logs/` state persistence (especially biweekly gating and auto-retire counters).

### 3.5 Timezone

All Task Scheduler triggers use **local PC time** (Eastern). Azure timers should use explicit timezone (`Eastern Standard Time`) or UTC with documented offset.

---

## 4. Complete job inventory

### 4.1 Primary table — Task Scheduler registered jobs

| # | Task Scheduler name | Schedule (local) | Recurrence | Auto-retire | Job entry point | Launcher `.bat` | Register script |
|---|---------------------|------------------|------------|-------------|-----------------|-----------------|-----------------|
| 1 | `GenesysToolkit-WeeklyQueueStaffingGap` | Mon **3:00 AM** | Weekly | **None** (ongoing) | `jobs/weekly_queue_staffing_gap_report.py` | `run_weekly_queue_staffing_gap.bat` | `scripts/register_weekly_queue_staffing_gap_task.ps1` |
| 2 | `GenesysToolkit-WeeklyDirectOutbound` | Mon **5:00 AM** | Weekly | **5 runs** | `jobs/weekly_direct_outbound_report.py` | `run_weekly_direct_outbound.bat` | `scripts/register_weekly_direct_outbound_task.ps1` |
| 3 | `GenesysToolkit-WeeklySpamAbandon` | Mon **6:00 AM** | Weekly | **8 runs** | `jobs/weekly_spam_abandon_report.py` | `run_weekly_spam_abandon.bat` | `scripts/register_weekly_spam_abandon_task.ps1` |
| 4 | `GenesysToolkit-DailyCareNavigatorMultiQueue` | **Daily 6:00 AM** | Daily | **10 runs** | `jobs/daily_care_navigator_multi_queue_report.py` | `run_daily_care_navigator_multi_queue.bat` | `scripts/register_daily_care_navigator_multi_queue_task.ps1` |
| 5 | `GenesysToolkit-WeeklyQueuePerformancePdf` | Mon **6:30 AM** | Weekly | None | `jobs/weekly_queue_performance_pdf.py` | `run_weekly_queue_performance_pdf.bat` | `scripts/register_weekly_queue_performance_pdf_task.ps1` |
| 6 | `GenesysToolkit-WeeklyWrapupTimeout` | Mon **7:00 AM** | Weekly | **8 runs** | `jobs/weekly_wrapup_timeout_report.py` | `run_weekly_wrapup_timeout.bat` | `scripts/register_weekly_wrapup_timeout_task.ps1` |
| 7 | `GenesysToolkit-WeeklyRepeatCaller` | Mon **8:00 AM** | Weekly | **8 runs** | `jobs/weekly_repeat_caller_report.py` | `run_weekly_repeat_caller.bat` | `scripts/register_weekly_repeat_caller_task.ps1` |
| 8 | `GenesysToolkit-WeeklyCallbackConversion` | Mon **8:30 AM** | Weekly | **8 runs** | `jobs/weekly_callback_conversion_report.py` | `run_weekly_callback_conversion.bat` | `scripts/register_weekly_callback_conversion_task.ps1` |
| 9 | `GenesysToolkit-BiweeklyAhtTrend` | Mon **9:00 AM** | Weekly trigger; job **skips** if last success &lt; 13 days | None (biweekly gate in job) | `jobs/biweekly_aht_trend_report.py` | `run_biweekly_aht_trend.bat` | `scripts/register_biweekly_aht_trend_task.ps1` |
| 10 | `GenesysToolkit-DirectOutbound-Business7d-1500` | **One-time** today 3:00 PM | One-shot | N/A (task deleted after run or manual) | `run_business7d_direct_outbound.bat` (inline report + email) | `run_business7d_direct_outbound.bat` | `scripts/register_3pm_business7d_outbound_task.ps1` |

**Monday morning sequence (when all weekly jobs are active):** 3:00 → 5:00 → 6:00 → 6:30 → 7:00 → 8:00 → 8:30 → 9:00 AM.

### 4.2 Per-job detail

#### 1 — Weekly Queue Staffing Gap

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/queue_staffing_gap_report.py` |
| **Output** | `outputs/queue_staffing_gap_weekly_{stamp}.xlsx` + `_meta.json` |
| **Log** | `logs/weekly_queue_staffing_gap_{stamp}.log` |
| **State** | `logs/weekly_queue_staffing_gap_schedule.json` |
| **Window** | Prior Mon–Sun |
| **Email** | Yes → `doug.hendrickson@archwellhealth.com` via `doug.env` |
| **Extra config** | `config/staffing_queue_defaults.csv`, `config/staffing_role_expectations.csv` |
| **Notes** | Phase 1 PS queue Erlang C staffing gap; **ongoing** (no auto-retire) |

#### 2 — Weekly Direct Outbound

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/direct_outbound_agents_report.py` (`--period previous-week --adaptive-threshold`) |
| **Output** | `outputs/direct_outbound_agents_weekly_{stamp}.xlsx` + `_meta.json` |
| **Log** | `logs/weekly_direct_outbound_{stamp}.log` |
| **State** | `logs/weekly_direct_outbound_schedule.json` (`max_runs: 5`) |
| **Window** | Prior Mon–Sun |
| **Email** | Yes |
| **Auto-retire** | After 5 successful runs, job calls `schtasks /Delete` on itself |

#### 3 — Weekly Spam / Short Abandon

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/spam_short_abandon_weekly_report.py` |
| **Output** | `outputs/spam_short_abandon_weekly_{stamp}.xlsx` + `_meta.json` |
| **Log** | `logs/weekly_spam_abandon_{stamp}.log` |
| **State** | `logs/weekly_spam_abandon_schedule.json` (`max_runs: 8`) |
| **Window** | Prior Mon–Sun |
| **Email** | Yes |
| **Auto-retire** | 8 runs |

#### 4 — Daily Care Navigator Multi-Queue

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/care_navigator_multi_queue_report.py` |
| **Output** | `outputs/care_navigator_multi_queue_{stamp}.xlsx` + `_meta.json` |
| **Log** | `logs/daily_care_navigator_multi_queue_{stamp}.log` |
| **State** | `logs/daily_care_navigator_multi_queue_schedule.json` (`max_runs: 10`) |
| **Window** | Current-day snapshot (ACTIVE users, title contains "Care Navigator", joined on 2+ queues) |
| **Email** | Yes |
| **Auto-retire** | 10 daily runs |

#### 5 — Weekly Queue Performance PDF

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/queue_performance_pdf_report.py` |
| **Output** | Local: `outputs/queue_performance_{dates}_{stamp}.pdf`; copy to shared folder |
| **Shared folder** | `QUEUE_PERFORMANCE_EXPORT_DIR` env, or `outputs/queue_performance_exports/` |
| **Log** | `logs/weekly_queue_performance_pdf_{stamp}.log` |
| **State** | `logs/weekly_queue_performance_pdf_schedule.json` |
| **Window** | Prior Mon–Sun; default queue prefix `Patient Services` |
| **Email** | **No** — file drop to shared folder only |
| **Notes** | Set `$SharedFolder` in register script or `QUEUE_PERFORMANCE_EXPORT_DIR` on Azure |

#### 6 — Weekly Wrap-Up Timeout

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/wrapup_timeout_weekly_report.py` |
| **Output** | `outputs/wrapup_timeout_weekly_{stamp}.xlsx` + `_meta.json` |
| **Log** | `logs/weekly_wrapup_timeout_{stamp}.log` |
| **State** | `logs/weekly_wrapup_timeout_schedule.json` (`max_runs: 8`) |
| **Window** | Prior Mon–Sun; wrap code `ININ-WRAP-UP-TIMEOUT` |
| **Email** | Yes |
| **Auto-retire** | 8 runs |

#### 7 — Weekly Repeat Caller

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/repeat_caller_weekly_report.py` |
| **Output** | `outputs/repeat_caller_weekly_{stamp}.xlsx` + `_meta.json` |
| **Log** | `logs/weekly_repeat_caller_{stamp}.log` |
| **State** | `logs/weekly_repeat_caller_schedule.json` (`max_runs: 8`) |
| **Window** | Prior Mon–Sun vs prior week comparison |
| **Email** | Yes |
| **Auto-retire** | 8 runs |

#### 8 — Weekly Callback Conversion

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/callback_conversion_weekly_report.py` |
| **Output** | `outputs/callback_conversion_weekly_{stamp}.xlsx` + `_meta.json` |
| **Log** | `logs/weekly_callback_conversion_{stamp}.log` |
| **State** | `logs/weekly_callback_conversion_schedule.json` (`max_runs: 8`) |
| **Window** | Prior Mon–Sun |
| **Email** | Yes |
| **Auto-retire** | 8 runs |

#### 9 — Biweekly AHT Trend

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/aht_trend_biweekly_report.py` |
| **Output** | `outputs/aht_trend_biweekly_{stamp}.html` + `_meta.json` |
| **Log** | `logs/biweekly_aht_trend_{stamp}.log` |
| **State** | `logs/biweekly_aht_trend_schedule.json` (`min_days_between_runs: 13`, `last_success_at`) |
| **Window** | 4 complete weeks |
| **Email** | Yes (HTML attachment) |
| **Gating** | Runs weekly trigger but **skips** if last success within 13 days; use `--force` to override |

#### 10 — Business 7-Day Direct Outbound (tactical)

| Field | Value |
|-------|-------|
| **Report tool** | `tools/reporting/direct_outbound_agents_report.py` (`--period business-days --days 7 --adaptive-threshold`) |
| **Output** | `outputs/direct_outbound_agents_business7d_{stamp}.xlsx` |
| **Log** | `logs/business7d_direct_outbound_{stamp}.log` |
| **Email** | Yes (inline in `.bat`, not via job wrapper) |
| **Schedule** | **One-time** registration for today 3:00 PM; helper `scripts/wait_until_3pm_business7d_outbound.ps1` for manual wait-and-run |
| **Notes** | Not a standing recurring schedule — re-register or use Azure timer if recurring 3 PM refresh is needed |

### 4.3 Related automation — not Task Scheduler registered

These exist in `jobs/` or as `.bat` launchers but **have no `scripts/register_*_task.ps1`** in the repo. They may run manually or via ad-hoc scheduling.

| Item | Launcher / entry | Purpose | Dependencies |
|------|------------------|---------|--------------|
| **Nightly rollup** | `run_nightly_rollup.bat` | MySQL rollup: queue/agent daily metrics, division backfill, retention, log cleanup | `client.env` MySQL vars; `jobs/build_queue_daily_metrics.py`, `build_agent_daily_metrics_sqlalchemy.py`, `backfill_queue_daily_metrics_divisions.py`, `cleanup_mysql_retention.py` |
| **Site picker** | `run_site_picker.bat` | Local Flask dev server | Not a scheduled report |
| **Data collection** | `jobs/collect_reporting_data.py` | Raw analytics ingestion | MySQL + Genesys OAuth |
| **Cache sync** | `jobs/sync_genesys_queues_cache.py`, `jobs/sync_genesys_users_cache.py` | API cache refresh | Genesys OAuth |
| **Queue alert monitor** | `docs/queue_alert_monitor_design.md` | **Design only** — not implemented | Future Azure cron candidate |

### 4.4 Email vs file-delivery summary

| Delivery | Jobs |
|----------|------|
| **Email** (9 jobs) | Direct Outbound, Spam Abandon, Wrapup Timeout, Repeat Caller, Callback Conversion, Queue Staffing Gap, Biweekly AHT, Daily Care Navigator, Business7d |
| **Shared folder only** | Queue Performance PDF |
| **Neither** | Nightly rollup (DB maintenance) |

---

## 5. Recommended Azure architecture for scheduled jobs

### 5.1 Option A — Azure App Service + WebJobs (recommended starting point)

```
┌─────────────────────────────────────────────────────────────┐
│  Azure App Service (Linux, Python 3.11+)                     │
│  ┌─────────────────────┐  ┌──────────────────────────────┐  │
│  │  Gunicorn / Flask    │  │  WebJobs (cron triggers)      │  │
│  │  Ops dashboard UI    │  │  python jobs/weekly_*.py      │  │
│  └─────────────────────┘  └──────────────────────────────┘  │
│           │                          │                        │
│           └──────────┬───────────────┘                        │
│                      ▼                                        │
│     Azure Files mount: /home/logs  /home/outputs              │
│     App Settings ← Key Vault refs (OAuth, SMTP)               │
└─────────────────────────────────────────────────────────────┘
          │                              │
          ▼                              ▼
   Genesys Cloud API              SMTP / Graph / SharePoint
   (*.pure.cloud)                  (work-approved)
```

**Why:** Same codebase and env as the web app; cron maps 1:1 to current Monday schedule; persistent `logs/` for biweekly gate and run counters.

**WebJob cron examples** (UTC — adjust for Eastern):

| Job | CRON (UTC example) | Eastern equivalent |
|-----|-------------------|-------------------|
| Queue Staffing Gap | `0 8 * * 1` | Mon 3 AM EST (UTC-5) / 4 AM EDT |
| Direct Outbound | `0 10 * * 1` | Mon 5 AM EST |
| Spam Abandon | `0 11 * * 1` | Mon 6 AM EST |
| Care Navigator | `0 11 * * *` | Daily 6 AM EST |
| Queue Perf PDF | `0 11 * * 1` + 30 min offset | Mon 6:30 AM EST |
| Wrapup Timeout | `0 12 * * 1` | Mon 7 AM EST |
| Repeat Caller | `0 13 * * 1` | Mon 8 AM EST |
| Callback Conversion | `0 13 * * 1` + 30 min | Mon 8:30 AM EST |
| Biweekly AHT | `0 14 * * 1` | Mon 9 AM EST |

Use `WEBSITE_TIME_ZONE=Eastern Standard Time` on App Service so CRON can stay in local time.

### 5.2 Option B — Azure Functions (Timer Trigger)

One function app per job group, shared class library. Good isolation and per-job scaling; more deployment overhead.

### 5.3 Option C — Azure Container Apps Jobs

Cron jobs in a container built from the same repo. Best if IT prefers container standard over WebJobs.

### 5.4 Option D — Dedicated Windows VM + Task Scheduler

Minimal code change but **not recommended** — repeats personal-PC failure modes (login required, single machine dependency).

### 5.5 Cross-cutting Azure requirements

| Concern | Recommendation |
|---------|----------------|
| **Secrets** | Azure Key Vault → App Service Key Vault references for `GENESYS_*`, SMTP creds |
| **State files** | Azure Files mount at `logs/`; do not rely on ephemeral local disk |
| **Outputs** | Azure Files or Blob; PDF job needs SharePoint/Azure Files UNC equivalent |
| **Email** | Work O365 SMTP relay or Graph API; env vars `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD` (future adapter) or extend `gmail_send.py` |
| **Genesys OAuth** | Same Client Credentials client; update allowed roles if IP-restricted |
| **Auto-retire** | Replace `schtasks /Delete` with state-only retirement (disable timer in Azure portal / remove WebJob CRON) |
| **Timeout** | Report jobs may run 5–20+ minutes; set WebJob/function timeout ≥ 30 min |
| **Always On** | Enable on App Service tier that supports WebJobs + long runs |

---

## 6. Migration checklist — global (once)

- [ ] **1.** Provision Azure App Service (Python 3.11+, Always On, sufficient SKU for WebJobs).
- [ ] **2.** Create Key Vault; store `GENESYS_CLIENT_ID`, `GENESYS_CLIENT_SECRET`, `GENESYS_REGION`, email credentials.
- [ ] **3.** Map Key Vault secrets to App Service application settings (and `client.env` equivalent).
- [ ] **4.** Mount Azure Files for `logs/` and `outputs/` (or configure Blob adapter).
- [ ] **5.** Deploy codebase (`pip install -r requirements.txt` on startup).
- [ ] **6.** Confirm Genesys Client Credentials grant from Azure outbound IP.
- [ ] **7.** Configure work email delivery (O365 SMTP or Graph); test send to `doug.hendrickson@archwellhealth.com`.
- [ ] **8.** Set `QUEUE_PERFORMANCE_EXPORT_DIR` to team SharePoint sync path or Azure Files share.
- [ ] **9.** Deploy WebJob CRON triggers matching Section 5.1 table.
- [ ] **10.** Copy existing `logs/*_schedule.json` state files if preserving run counts / biweekly gate.
- [ ] **11.** Run parallel shadow week: Azure jobs `--dry-run-email` while PC still runs (optional).
- [ ] **12.** Cut over: disable personal PC tasks (Section 8).
- [ ] **13.** Establish monthly zip handoff to IT (Section 10).
- [ ] **14.** Update Genesys OAuth redirect URIs if web app hostname changes.

---

## 7. Migration checklist — per job

| Job | Migrate action | Azure timer | Pre-cutover test | Post-cutover verify | Retire PC task |
|-----|----------------|-------------|------------------|---------------------|----------------|
| **Queue Staffing Gap** | WebJob → `python jobs/weekly_queue_staffing_gap_report.py` | Mon 3:00 AM ET | `--dry-run-email` | Email + xlsx; check Erlang sheets | Disable `GenesysToolkit-WeeklyQueueStaffingGap` |
| **Direct Outbound** | Same pattern | Mon 5:00 AM ET | `--dry-run-email` | Email + adaptive threshold meta | Disable after Azure stable (or let auto-retire finish on PC) |
| **Spam Abandon** | Same | Mon 6:00 AM ET | `--dry-run-email` | By Queue + Repeat ANIs sheets | Disable |
| **Care Navigator** | Same | Daily 6:00 AM ET | `--dry-run-email` | Agent count in subject | Disable |
| **Queue Perf PDF** | WebJob; set `QUEUE_PERFORMANCE_EXPORT_DIR` | Mon 6:30 AM ET | `--dry-run` | PDF in shared folder | Disable |
| **Wrapup Timeout** | Same | Mon 7:00 AM ET | `--dry-run-email` | ININ-WRAP-UP-TIMEOUT agents | Disable |
| **Repeat Caller** | Same | Mon 8:00 AM ET | `--dry-run-email` | WoW comparison sheet | Disable |
| **Callback Conversion** | Same | Mon 8:30 AM ET | `--dry-run-email` | Callbacks Created metrics | Disable |
| **Biweekly AHT** | Same; **preserve** `logs/biweekly_aht_trend_schedule.json` | Mon 9:00 AM ET | `--dry-run-email`; test `--force` | HTML opens; 13-day skip works | Disable |
| **Business7d 3 PM** | Timer trigger or manual ops runbook | As needed (not weekly) | Run bat manually once on Azure | 7 business day window | Delete one-time PC task if present |

### 7.1 Code adjustments for Azure (recommended follow-ups)

| Issue | Current behavior | Azure fix |
|-------|------------------|-----------|
| Auto-retire | `schtasks /Delete` (Windows only) | Disable WebJob CRON when `runs_completed >= max_runs`; skip schtasks on non-Windows |
| Hardcoded PC paths | Register scripts use `C:\Users\douglash\...` | Use `PROJECT_ROOT`-relative paths only (jobs already do) |
| Email | Personal Gmail only | Add O365 SMTP env vars to `gmail_send.py` or new `tools/email/work_send.py` |
| PDF shared folder | UNC / local path | Azure Files mount or SharePoint Graph upload |

---

## 8. Stop running on personal PC

After Azure jobs succeed for one full cycle:

### 8.1 List registered tasks

```powershell
Get-ScheduledTask -TaskName "GenesysToolkit-*" | Format-Table TaskName, State
```

### 8.2 Disable (reversible)

```powershell
$tasks = @(
  "GenesysToolkit-WeeklyQueueStaffingGap",
  "GenesysToolkit-WeeklyDirectOutbound",
  "GenesysToolkit-WeeklySpamAbandon",
  "GenesysToolkit-DailyCareNavigatorMultiQueue",
  "GenesysToolkit-WeeklyQueuePerformancePdf",
  "GenesysToolkit-WeeklyWrapupTimeout",
  "GenesysToolkit-WeeklyRepeatCaller",
  "GenesysToolkit-WeeklyCallbackConversion",
  "GenesysToolkit-BiweeklyAhtTrend",
  "GenesysToolkit-DirectOutbound-Business7d-1500"
)
foreach ($t in $tasks) {
  Disable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue
}
```

### 8.3 Remove permanently

```powershell
Unregister-ScheduledTask -TaskName "<TaskName>" -Confirm:$false
```

Or re-run any `scripts/register_*_task.ps1` only when intentionally re-registering on PC.

**Important:** Do not disable PC tasks until Azure shadow run confirms email/PDF delivery.

---

## 9. Post-migration verification checklist

### 9.1 Infrastructure

- [ ] App Service healthy; WebJobs show last run succeeded
- [ ] Key Vault secrets resolve (no startup auth errors in Log stream)
- [ ] Azure Files mount writable from WebJob
- [ ] `python core/verify_auth.py` succeeds on Azure (Kudu SSH or one-off command)

### 9.2 Per weekly cycle (Monday)

- [ ] Queue Staffing Gap email received by 4:00 AM ET
- [ ] Direct Outbound email by 6:00 AM ET
- [ ] Spam Abandon email by 7:00 AM ET
- [ ] Queue Performance PDF in shared folder by 7:00 AM ET
- [ ] Wrapup Timeout email by 8:00 AM ET
- [ ] Repeat Caller email by 9:00 AM ET
- [ ] Callback Conversion email by 9:30 AM ET
- [ ] Biweekly AHT email on alternating weeks by 10:00 AM ET

### 9.3 Daily

- [ ] Care Navigator email by 7:00 AM ET (while job remains active)

### 9.4 Log / state review

- [ ] New entries in `logs/weekly_*_{stamp}.log` with exit success
- [ ] `logs/*_schedule.json` counters increment
- [ ] No duplicate emails from both PC and Azure

---

## 10. Monthly zip handoff pattern (work ownership)

After Azure cutover, GenesysCloudToolkit on the work domain is **IT/work-owned**. Doug delivers monthly deployable updates.

Each monthly zip must include:

1. **Deployable release** — full app + `jobs/` + `tools/` + `requirements.txt`
2. **Setup / operations doc** with at minimum:
   - How to configure/reset OAuth variables in Azure App Service
   - How to update matching OAuth settings in Genesys Cloud (redirect URIs, client credentials)
   - Startup command, Python version, `pip install` steps
   - WebJob CRON schedule table (Section 5.1)
   - Email/SMTP configuration steps
   - Post-deploy smoke test checklist (Section 9)

**Doug action items after Azure setup:**

1. Establish monthly handoff calendar with IT
2. Co-author setup doc template in IT's required format
3. Treat production on work domain as work-owned (not personal side project)

---

## 11. Operational runbook

### 11.1 Manual re-run (from dev PC or Kudu)

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"

# Dry-run (generate report, preview email, do not send or increment retire counter for --dry-run-email jobs)
python jobs/weekly_direct_outbound_report.py --dry-run-email
python jobs/weekly_spam_abandon_report.py --dry-run-email
python jobs/weekly_wrapup_timeout_report.py --dry-run-email
python jobs/weekly_repeat_caller_report.py --dry-run-email
python jobs/weekly_callback_conversion_report.py --dry-run-email
python jobs/weekly_queue_staffing_gap_report.py --dry-run-email
python jobs/daily_care_navigator_multi_queue_report.py --dry-run-email
python jobs/biweekly_aht_trend_report.py --dry-run-email
python jobs/weekly_queue_performance_pdf.py --dry-run

# Full run (generates + emails)
python jobs/weekly_direct_outbound_report.py

# Skip email, report only
python jobs/weekly_spam_abandon_report.py --skip-email

# Biweekly: force run despite 13-day gate
python jobs/biweekly_aht_trend_report.py --force

# Business 7-day tactical report
.\run_business7d_direct_outbound.bat
```

### 11.2 Re-register on personal PC (pre-migration only)

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
powershell -File scripts/register_weekly_direct_outbound_task.ps1
powershell -File scripts/register_weekly_spam_abandon_task.ps1
powershell -File scripts/register_weekly_wrapup_timeout_task.ps1
powershell -File scripts/register_weekly_repeat_caller_task.ps1
powershell -File scripts/register_weekly_callback_conversion_task.ps1
powershell -File scripts/register_weekly_queue_staffing_gap_task.ps1
powershell -File scripts/register_weekly_queue_performance_pdf_task.ps1
powershell -File scripts/register_biweekly_aht_trend_task.ps1
powershell -File scripts/register_daily_care_navigator_multi_queue_task.ps1
powershell -File scripts/register_3pm_business7d_outbound_task.ps1  # one-time, before 3 PM
```

### 11.3 Check schedule state / run counts

```powershell
Get-Content "logs\weekly_direct_outbound_schedule.json"
Get-Content "logs\biweekly_aht_trend_schedule.json"
```

### 11.4 Send ad-hoc email with attachment

```powershell
python tools/email/gmail_send.py --env-file doug.env `
  --to doug.hendrickson@archwellhealth.com `
  --subject "Manual report" `
  --body "See attached." `
  --attach "outputs\<filename>.xlsx"
```

### 11.5 Verify Genesys auth

```powershell
python core/verify_auth.py
python core/verify_auth.py --env-file client.env
```

### 11.6 Nightly rollup (manual, if MySQL pipeline used on work host)

```powershell
.\run_nightly_rollup.bat
```

---

## 12. Reference — file map

| Category | Files |
|----------|-------|
| **Job entry points** | `jobs/weekly_*.py`, `jobs/biweekly_aht_trend_report.py`, `jobs/daily_care_navigator_multi_queue_report.py` |
| **Launchers** | `run_weekly_*.bat`, `run_biweekly_aht_trend.bat`, `run_daily_care_navigator_multi_queue.bat`, `run_business7d_direct_outbound.bat`, `run_weekly_queue_performance_pdf.bat` |
| **Task registration** | `scripts/register_*_task.ps1` (10 scripts) |
| **Report tools** | `tools/reporting/*.py` |
| **Email** | `tools/email/gmail_send.py`, `tools/email/GMAIL_SETUP.md`, `doug.env` |
| **OAuth** | `client.env`, `core/config.py`, `core/verify_auth.py` |
| **State / logs** | `logs/*_schedule.json`, `logs/*_{stamp}.log` |
| **Outputs** | `outputs/*.xlsx`, `outputs/*.html`, `outputs/queue_performance_exports/*.pdf` |
| **Staffing config** | `config/staffing_queue_defaults.csv`, `config/staffing_role_expectations.csv` |
| **Related design** | `docs/staffing_burden_report_design.md`, `_generate_azure_hosting_doc.py` |

---

## 13. Document history

| Date | Author | Change |
|------|--------|--------|
| 2026-09-09 | GenesysCloudToolkit / Cursor agent | Initial inventory and migration guide from repo file audit |
