# Report Data Retention & Searchable History Store

**Project:** GenesysCloudToolkit  
**Document date:** 2026-09-09  
**Author:** Cursor agent (design review for Doug)  
**Status:** Design only — no implementation in this pass

---

## 1. Executive summary

Today, scheduled and ad-hoc reports write **Excel/HTML/PDF artifacts** to `outputs/` with optional `{stem}_meta.json` sidecars. Job orchestration state lives in `logs/*_schedule.json`. Nothing beyond ~90 days of XLSX files provides **queryable history** for license utilization (CX2 cost-per-minute, zero-usage reconciliation) or similar periodic reports.

**Recommendation:** Extend the **existing MySQL pipeline** (same engine as `queue_daily_metrics` / `agent_daily_metrics`) with report-specific tables. Run locally via `docker-compose.mysql.yml` + `client.env` today; migrate to **Azure Database for MySQL** (or Azure SQL via existing `core/db.py` dialect switch) at IT handoff. Append structured rows after each CX2 report run; keep XLSX as the human deliverable.

This aligns with nightly rollup patterns, reuses `cleanup_mysql_retention.py`, and avoids introducing a second storage stack (SQLite file, Parquet lake, etc.) until volume or IT requirements justify it.

---

## 2. Current storage patterns (repo audit)

### 2.1 MySQL — primary structured store (on hold for dashboards, active for rollups)

| Component | Path | Role |
|-----------|------|------|
| Schema bootstrap | `schema/schema.sql` | `gc_users`, `gc_queues`, `gc_conversations`, `tool_runs`, cache tables |
| DB engine | `core/db.py` | SQLAlchemy; MySQL default; SQL Server path documented for Azure |
| Queue rollups | `jobs/build_queue_daily_metrics.py`, `build_queue_daily_metrics_sqlalchemy.py` | `queue_daily_metrics` (daily grain, PK `metric_date + queue_id`) |
| Agent rollups | `jobs/build_agent_daily_metrics_sqlalchemy.py` | `agent_daily_metrics` (daily grain, PK `metric_date + queue_id + agent_id`) |
| Retention | `jobs/cleanup_mysql_retention.py` | `queue_daily_metrics`: **1095d**; `gc_conversations`: **180d** |
| Nightly launcher | `run_nightly_rollup.bat` | Rollups + retention + log cleanup (**90d** rollup logs) |
| Local MySQL | `docker-compose.mysql.yml` | MySQL 8.4 container |
| Env | `client.env.example` | `MYSQL_HOST`, `MYSQL_DATABASE`, etc. |
| Dashboard query example | `dashboards/agent/app.py` | Date-range aggregation over `agent_daily_metrics` |

**Key pattern:** Daily fact tables with idempotent upsert over a sliding backfill window (`--days-back N`), plus table-specific retention in cleanup job.

### 2.2 Weekly / scheduled reports — file + JSON state only

| Pattern | Location | Contents |
|---------|----------|----------|
| Report output | `outputs/<name>_{stamp}.xlsx` | Full report (primary deliverable) |
| Run metadata | `outputs/<name>_{stamp}_meta.json` | Thresholds, counts, window labels |
| Schedule state | `logs/<job>_schedule.json` | `runs_completed`, `max_runs`, `last_run_at`, biweekly `last_success_at` |
| Run logs | `logs/<job>_{stamp}.log` | stdout/stderr capture |

Documented in `docs/scheduled_reports_migration_guide_20260909.md`. Azure migration assumes **Azure Files** for `logs/` and `outputs/` persistence; no structured query layer for historical report metrics.

### 2.3 CX2 reports — Genesys Analytics → XLSX only

| Script | Default windows | Sheets / facts |
|--------|-----------------|----------------|
| `tools/reporting/cx2_cost_per_minute_report.py` | Daily **30d**, summary **90d** | `Daily_30d`, `Summary_90d`, `Inactive_Waste` |
| `tools/reporting/cx2_zero_usage_reconciliation.py` | Summary **90d** | `Summary`, `Zero_Usage_90d`, `Bucket_Summary` |

Metrics source: Genesys Analytics API (`tHandle`, voice, inbound/outbound). Agent dimensions: name, email, user_id, title, market (site prefix), site_name, site_code, state. License cost default: **$156.40/mo** per CX2 seat.

**Gap:** Each run re-queries Genesys for the same rolling windows. Older daily rows (days 31–365+) are never retained locally. Cannot answer “180-day avg $/min for market CLE as of 2026-06-01” without re-pulling Genesys history (90d API practical limit per run) or storing snapshots over time.

### 2.4 Azure migration context

From `docs/toolkit_ops_demo_migration_recommendations_20260909.md` and `scheduled_reports_migration_guide_20260909.md`:

- MySQL cache/sync/metrics pipeline rated **P3 — after Azure MySQL provisioned**
- `core/db.py` is the intended **Azure production DB layer**
- Monthly IT zip handoff; minimal Doug ops until handoff

---

## 3. Requirements — report history store

### 3.1 Functional

| ID | Requirement |
|----|-------------|
| F1 | **Time-series snapshots:** Persist agent-level CX2 handle minutes and derived $/min at **daily grain** beyond the 30-day report tab |
| F2 | **Periodic reconciliation buckets:** Store each zero-usage reconciliation run (user, bucket, telephony footprint, org-level CPM) |
| F3 | **Point-in-time windows:** Query “as-of date” rolling **180d** and **365d** windows using stored daily facts + snapshot tables |
| F4 | **Search / filter:** By agent (name/email/id), market, title, site, bucket, date range, report run |
| F5 | **Trend queries:** License waste seat count over months; org CPM drift; users with zero usage N consecutive months |
| F6 | **Extensible:** Same `report_runs` + daily-fact pattern usable for future reports (direct outbound weekly aggregates, staffing gap, etc.) |

### 3.2 Non-functional

| ID | Requirement |
|----|-------------|
| NF1 | **Retention:** ≥ **365 days** queryable; **1095 days** preferred for rollups (match `queue_daily_metrics`) |
| NF2 | **Local now:** Works on Doug’s PC with Docker MySQL or existing local instance; no IT dependency |
| NF3 | **Azure later:** Connection string swap only; no schema redesign |
| NF4 | **Minimal ops:** Single retention script extension; no manual DBA |
| NF5 | **Idempotent ingestion:** Re-running same report date/run does not duplicate rows |
| NF6 | **Deliverable unchanged:** XLSX email workflow remains; DB is additive |

### 3.3 Data semantics

| Concept | Definition |
|---------|------------|
| **metric_date** | Calendar date (America/New_York business alignment — same as CX2 report `TZ_OFFSET_HOURS`) |
| **as_of_date** | End date of the analytics window for a report run (typically “today” or last complete day) |
| **snapshot window** | Rolling N-day aggregate computed at `as_of_date` (90, 180, 365) |
| **run_id** | UUID or `{report_type}_{as_of_date}_{utc_timestamp}` — unique per execution |

**Point-in-time vs rolling:** Daily facts are stored by `metric_date` (immutable day bucket). Rolling 180d/365d metrics are **computed at query time** from daily facts where possible, or read from **precomputed snapshot rows** attached to each `report_runs` record for exact reproduction of emailed reports.

---

## 4. Storage options compared

### Option A — SQLite single file (`data/report_history.db`)

| Pros | Cons |
|------|------|
| Zero install beyond Python | **New stack** parallel to MySQL; splits retention/backup |
| Trivial copy/backup | Weaker concurrent write story if web app queries later |
| Good for solo PC | Azure App Service: file on Azure Files works but **not** IT’s likely DB choice |
| | No reuse of `core/db.py`, `cleanup_mysql_retention.py`, dashboard patterns |

**Verdict:** Fast POC only; not aligned with existing toolkit direction.

### Option B — Extend MySQL (recommended)

| Pros | Cons |
|------|------|
| Matches `agent_daily_metrics` / `queue_daily_metrics` patterns | Requires MySQL running locally (Docker compose exists) |
| Reuses `core/db.py`, SQLAlchemy, retention job | Slightly heavier than SQLite for “DB off” dev |
| Azure Database for MySQL is natural P3 migration path | Doug must start nightly rollup or enable MySQL for CX2 ingest |
| Dashboards can query CX2 history later | |
| `tool_runs` / schema versioning already in `schema/schema.sql` | |

**Verdict:** **Primary recommendation.**

### Option C — Parquet files + DuckDB (analytical lake)

| Pros | Cons |
|------|------|
| Excellent columnar compression; fast 365d scans | **New dependencies** (`duckdb`, `pyarrow`) not in `requirements.txt` |
| Cheap cold storage on Azure Blob | No existing ingest/query conventions in repo |
| | Harder relational filters (agent/market/title) without careful partitioning |
| | IT handoff format unclear |

**Verdict:** Consider if CX2+conversation history exceeds ~10M rows/year or for cross-report data science; overkill for Phase 1.

### Option D — Azure Blob (XLSX archive) + index table

| Pros | Cons |
|------|------|
| Preserves exact emailed artifacts | **Not searchable** for agent-level trends without parsing XLSX each time |
| Simple Azure story | Slow queries; brittle to sheet layout changes |
| | Does not solve 180d/365d aggregation |

**Verdict:** Complement only (optional blob copy of XLSX); not primary analytics store.

---

## 5. Recommended architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Report tools (cx2_cost_per_minute_report.py,                   │
│               cx2_zero_usage_reconciliation.py, future reports)  │
│       │                                                          │
│       ├──► outputs/*.xlsx  (human deliverable, email attach)     │
│       └──► core/report_history.py  (post-run ingest)             │
└───────────────────────────────┬─────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  MySQL (local Docker now → Azure Database for MySQL later)       │
│  report_runs │ cx2_agent_daily │ cx2_agent_snapshot │             │
│              │ cx2_zero_usage_reconciliation │ cx2_org_snapshot  │
└───────────────────────────────┬─────────────────────────────────┘
                                ▼
┌─────────────────────────────────────────────────────────────────┐
│  Query surfaces (Phase 2+)                                     │
│  • CLI: tools/reporting/query_report_history.py                  │
│  • Optional Flask/Ops Demo tab                                   │
│  • Ad-hoc SQL / Excel export                                     │
└─────────────────────────────────────────────────────────────────┘
                                ▲
┌───────────────────────────────┴─────────────────────────────────┐
│  jobs/cleanup_mysql_retention.py  (+ new table retention rules)  │
└─────────────────────────────────────────────────────────────────┘
```

### 5.1 Relationship to existing `tool_runs`

`schema/schema.sql` already defines `tool_runs` (run_id, tool_id, mode, status). **Option 5.1a (preferred):** Use `tool_runs` for all report executions; add `report_type` via `tool_id` values like `cx2_cost_per_minute`, `cx2_zero_usage_reconciliation`. **Option 5.1b:** Separate `report_runs` table with richer report-specific metadata. This design uses **`report_runs`** as specified for clarity and FK targets; implementation may merge into `tool_runs` if duplication is undesirable.

---

## 6. Schema sketch (CX2 minimum)

Migration file: `schema/migrations/002_report_history_cx2.sql` (proposed).

### 6.1 `report_runs`

One row per report execution.

```sql
CREATE TABLE IF NOT EXISTS report_runs (
    id              BIGINT PRIMARY KEY AUTO_INCREMENT,
    run_id          VARCHAR(64) NOT NULL,
    report_type     VARCHAR(64) NOT NULL,   -- cx2_cost_per_minute | cx2_zero_usage_reconciliation
    as_of_date      DATE NOT NULL,          -- window end date
    window_days     INT NULL,               -- primary summary window (90 default)
    started_at      DATETIME NOT NULL,
    finished_at     DATETIME NULL,
    status          VARCHAR(32) NOT NULL,   -- success | failed
    cx2_monthly_cost DECIMAL(10,2) NOT NULL DEFAULT 156.40,
    cx2_user_count  INT NULL,
    output_path     VARCHAR(512) NULL,
    meta_json       JSON NULL,              -- full meta dict from report (starts, thresholds, etc.)
    created_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_report_runs_run_id (run_id),
    INDEX idx_rr_type_as_of (report_type, as_of_date),
    INDEX idx_rr_started (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 6.2 `cx2_agent_daily`

Canonical daily handle-time facts (accumulated across runs; idempotent upsert by date+user).

```sql
CREATE TABLE IF NOT EXISTS cx2_agent_daily (
    metric_date         DATE NOT NULL,
    user_id             VARCHAR(64) NOT NULL,
    agent_name          VARCHAR(255) NULL,
    email               VARCHAR(255) NULL,
    title               VARCHAR(255) NULL,
    market              VARCHAR(16) NULL,
    site_name           VARCHAR(255) NULL,
    site_code           VARCHAR(16) NULL,
    user_state          VARCHAR(64) NULL,
    inbound_handle_ms   BIGINT NOT NULL DEFAULT 0,
    outbound_handle_ms  BIGINT NOT NULL DEFAULT 0,
    total_handle_ms     BIGINT NOT NULL DEFAULT 0,
    daily_license_cost  DECIMAL(10,2) NULL,
    cost_per_min_total  DECIMAL(12,4) NULL,
    cost_per_min_inbound  DECIMAL(12,4) NULL,
    cost_per_min_outbound DECIMAL(12,4) NULL,
    source_run_id       VARCHAR(64) NULL,   -- last run that wrote this row
    updated_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (metric_date, user_id),
    INDEX idx_cx2_daily_market_date (market, metric_date),
    INDEX idx_cx2_daily_user_date (user_id, metric_date),
    INDEX idx_cx2_daily_title (title(64))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

**Note:** Store milliseconds internally; report XLSX uses minutes (2 decimal). Conversion at ingest.

### 6.3 `cx2_agent_snapshot`

Period-level aggregates **as of each report run** (reproduces Summary_90d sheet; supports 180d/365d when those windows are run).

```sql
CREATE TABLE IF NOT EXISTS cx2_agent_snapshot (
    run_id                  VARCHAR(64) NOT NULL,
    as_of_date              DATE NOT NULL,
    window_days             INT NOT NULL,       -- 90, 180, 365
    user_id                 VARCHAR(64) NOT NULL,
    agent_name              VARCHAR(255) NULL,
    email                   VARCHAR(255) NULL,
    title                   VARCHAR(255) NULL,
    market                  VARCHAR(16) NULL,
    site_name               VARCHAR(255) NULL,
    site_code               VARCHAR(16) NULL,
    user_state              VARCHAR(64) NULL,
    inbound_handle_min      DECIMAL(12,2) NOT NULL DEFAULT 0,
    outbound_handle_min     DECIMAL(12,2) NOT NULL DEFAULT 0,
    total_handle_min        DECIMAL(12,2) NOT NULL DEFAULT 0,
    allocated_license_cost  DECIMAL(12,2) NULL,
    cost_per_min_total      DECIMAL(12,4) NULL,
    cost_per_min_inbound    DECIMAL(12,4) NULL,
    cost_per_min_outbound   DECIMAL(12,4) NULL,
    is_waste_flag           TINYINT(1) NOT NULL DEFAULT 0,  -- <5 min in window
    waste_reason            VARCHAR(255) NULL,
    PRIMARY KEY (run_id, user_id),
    INDEX idx_cx2_snap_as_of_window (as_of_date, window_days),
    INDEX idx_cx2_snap_market (market, as_of_date),
    INDEX idx_cx2_snap_cpm (cost_per_min_total),
    CONSTRAINT fk_cx2_snap_run FOREIGN KEY (run_id) REFERENCES report_runs(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 6.4 `cx2_zero_usage_reconciliation`

One row per zero-handle user per reconciliation run.

```sql
CREATE TABLE IF NOT EXISTS cx2_zero_usage_reconciliation (
    run_id              VARCHAR(64) NOT NULL,
    as_of_date          DATE NOT NULL,
    window_days         INT NOT NULL DEFAULT 90,
    user_id             VARCHAR(64) NOT NULL,
    agent_name          VARCHAR(255) NULL,
    email               VARCHAR(255) NULL,
    title               VARCHAR(255) NULL,
    user_state          VARCHAR(64) NULL,
    market              VARCHAR(16) NULL,
    site_code           VARCHAR(16) NULL,
    site_name           VARCHAR(255) NULL,
    phone_assigned      TINYINT(1) NOT NULL DEFAULT 0,
    phone_name          VARCHAR(255) NULL,
    phone_type          VARCHAR(32) NULL,
    primary_voice       VARCHAR(255) NULL,
    work_extension      VARCHAR(64) NULL,
    queue_count         INT NOT NULL DEFAULT 0,
    queue_memberships   TEXT NULL,
    last_login          VARCHAR(32) NULL,
    telephony_roles     TEXT NULL,
    all_roles           TEXT NULL,
    recommended_bucket  VARCHAR(32) NOT NULL,  -- TRUE_WASTE | PROVISIONING_GAP | ...
    monthly_license_cost DECIMAL(10,2) NULL,
    notes               TEXT NULL,
    PRIMARY KEY (run_id, user_id),
    INDEX idx_cx2_zero_as_of (as_of_date),
    INDEX idx_cx2_zero_bucket (recommended_bucket, as_of_date),
    INDEX idx_cx2_zero_market (market, as_of_date),
    CONSTRAINT fk_cx2_zero_run FOREIGN KEY (run_id) REFERENCES report_runs(run_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 6.5 `cx2_org_snapshot` (optional but high value)

Org-level metrics from reconciliation front sheet — enables trend without re-aggregation.

```sql
CREATE TABLE IF NOT EXISTS cx2_org_snapshot (
    run_id                      VARCHAR(64) NOT NULL PRIMARY KEY,
    as_of_date                  DATE NOT NULL,
    window_days                 INT NOT NULL,
    cx2_user_count              INT NOT NULL,
    zero_user_count             INT NOT NULL,
    true_waste_count            INT NOT NULL,
    cpm_full_org                DECIMAL(12,4) NULL,
    cpm_with_usage_only         DECIMAL(12,4) NULL,
    total_handle_minutes_all    DECIMAL(14,2) NULL,
    total_allocated_cost        DECIMAL(14,2) NULL,
    reclaimable_monthly_cost    DECIMAL(12,2) NULL,
    CONSTRAINT fk_cx2_org_run FOREIGN KEY (run_id) REFERENCES report_runs(run_id),
    INDEX idx_cx2_org_as_of (as_of_date)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

### 6.6 Volume estimate

Assumptions: ~400–600 CX2 users, daily ingest, monthly reconciliation.

| Table | Rows/year (order of magnitude) |
|-------|--------------------------------|
| `cx2_agent_daily` | 150k–220k (users × 365) |
| `cx2_agent_snapshot` | 5k–7k per window per run × runs/year |
| `cx2_zero_usage_reconciliation` | ~50–150 users × 12 runs ≈ 2k/year |

Well within MySQL comfort zone on a laptop or Azure burstable tier.

---

## 7. Ingestion pattern

### 7.1 Module layout (proposed)

```
core/report_history.py       # ingest helpers, run_id generation, upsert SQL
schema/migrations/002_...sql # DDL
jobs/apply_report_history_schema.py  # one-time / CI schema apply
```

### 7.2 Run ID generation

```python
run_id = f"cx2_cpm_{as_of_date.isoformat()}_{utc_timestamp}"  # e.g. cx2_cpm_2026-09-09_20260909T165432Z
```

Deterministic alternative for idempotent **daily scheduled** runs:

```python
run_id = f"cx2_cpm_{as_of_date.isoformat()}"  # one successful run per day max
```

Use deterministic `run_id` when schedule is fixed; include timestamp for ad-hoc reruns.

### 7.3 Hook points

**`cx2_cost_per_minute_report.py` — end of `run_report()` after `write_workbook()`:**

1. `run_id = generate_run_id("cx2_cost_per_minute", summary_end)`
2. `INSERT report_runs` (status=`success`, meta_json=meta, cx2_user_count=len(agents))
3. **Upsert `cx2_agent_daily`** for each daily row (30d window each run — rows outside window already historical)
4. **Insert `cx2_agent_snapshot`** for summary_rows (90d default; also run with `--days-summary 180` and `365` on monthly schedule)
5. Optionally insert waste rows into snapshot with `is_waste_flag=1`

**`cx2_zero_usage_reconciliation.py` — end of `run_report()`:**

1. `report_runs` row (`report_type=cx2_zero_usage_reconciliation`)
2. Bulk insert `cx2_zero_usage_reconciliation` rows
3. Insert `cx2_org_snapshot` from `org_metrics` + bucket counts

### 7.4 Idempotency rules

| Table | Strategy |
|-------|----------|
| `report_runs` | `UNIQUE(run_id)` — second insert skipped or `ON DUPLICATE KEY UPDATE status, finished_at` |
| `cx2_agent_daily` | `INSERT ... ON DUPLICATE KEY UPDATE` — newer `source_run_id` wins; metrics overwritten for same (date, user) |
| `cx2_agent_snapshot` | PK `(run_id, user_id)` — delete+insert or replace for same `run_id` |
| `cx2_zero_usage_reconciliation` | Same as snapshot — tied to `run_id` |

**CLI flag:** `--no-store` to skip DB write (dry-run / dev without MySQL).

**Failure handling:** If MySQL unavailable, log warning and still produce XLSX (non-blocking ingest).

### 7.5 Scheduled cadence suggestion

| Job | Cadence | Windows stored |
|-----|---------|------------------|
| CX2 cost-per-minute | Monthly (1st) or weekly | Daily upsert (30d pull) + snapshot 90d |
| CX2 extended window | Monthly | Additional snapshot runs: `--days-summary 180`, `--days-summary 365` |
| Zero-usage reconciliation | Monthly | 90d bucket snapshot + org snapshot |

Register Task Scheduler / Azure WebJob after CX2 reports stabilize.

### 7.6 Retention extension

Add to `jobs/cleanup_mysql_retention.py`:

| Table | Retention |
|-------|-----------|
| `cx2_agent_daily` | **1095 days** (metric_date) |
| `cx2_agent_snapshot` | **1095 days** (as_of_date) |
| `cx2_zero_usage_reconciliation` | **1095 days** (as_of_date) |
| `cx2_org_snapshot` | **1095 days** |
| `report_runs` | **1095 days** (started_at) or orphan-clean after child tables |

Optional: export Parquet/csv to `outputs/archive/` before delete (Phase 3).

---

## 8. Query examples

### 8.1 180-day average $/min for market CLE (computed from daily facts)

```sql
-- As-of :as_of_date, rolling 180 days, market CLE
SELECT
    SUM(d.total_handle_ms) / 60000.0 AS total_handle_min,
    COUNT(DISTINCT d.user_id) AS agent_count,
    SUM(156.40 * (180.0 / 30.0)) / NULLIF(SUM(d.total_handle_ms) / 60000.0, 0) AS org_cpm_approx
FROM cx2_agent_daily d
WHERE d.market = 'CLE'
  AND d.metric_date > DATE_SUB(:as_of_date, INTERVAL 180 DAY)
  AND d.metric_date <= :as_of_date;
```

Per-agent 180d CPM:

```sql
SELECT
    user_id,
    agent_name,
    SUM(total_handle_ms) / 60000.0 AS handle_min_180d,
    (156.40 * (180.0 / 30.0)) / NULLIF(SUM(total_handle_ms) / 60000.0, 0) AS cpm_180d
FROM cx2_agent_daily
WHERE market = 'CLE'
  AND metric_date > DATE_SUB(:as_of_date, INTERVAL 180 DAY)
  AND metric_date <= :as_of_date
GROUP BY user_id, agent_name
ORDER BY cpm_180d DESC;
```

### 8.2 Users with zero usage 3 months in a row

Using monthly reconciliation snapshots:

```sql
WITH monthly_zero AS (
    SELECT
        user_id,
        DATE_FORMAT(as_of_date, '%Y-%m') AS ym,
        MAX(CASE WHEN recommended_bucket = 'TRUE_WASTE' THEN 1 ELSE 0 END) AS is_true_waste
    FROM cx2_zero_usage_reconciliation
    WHERE as_of_date >= DATE_SUB(CURDATE(), INTERVAL 4 MONTH)
    GROUP BY user_id, DATE_FORMAT(as_of_date, '%Y-%m')
),
streak AS (
    SELECT user_id, COUNT(*) AS zero_months
    FROM monthly_zero
    GROUP BY user_id
    HAVING COUNT(*) >= 3
       AND SUM(is_true_waste) >= 3
)
SELECT z.user_id, z.agent_name, z.email, z.market, s.zero_months
FROM streak s
JOIN cx2_zero_usage_reconciliation z
  ON z.user_id = s.user_id
 AND z.as_of_date = (SELECT MAX(as_of_date) FROM cx2_zero_usage_reconciliation)
ORDER BY s.zero_months DESC, z.agent_name;
```

Alternative using daily facts (stricter — zero handle minutes each month):

```sql
SELECT user_id, agent_name,
       SUM(CASE WHEN metric_date >= DATE_SUB(CURDATE(), INTERVAL 30 DAY) THEN total_handle_ms END) AS ms_m0,
       SUM(CASE WHEN metric_date >= DATE_SUB(CURDATE(), INTERVAL 60 DAY)
                 AND metric_date < DATE_SUB(CURDATE(), INTERVAL 30 DAY) THEN total_handle_ms END) AS ms_m1,
       SUM(CASE WHEN metric_date >= DATE_SUB(CURDATE(), INTERVAL 90 DAY)
                 AND metric_date < DATE_SUB(CURDATE(), INTERVAL 60 DAY) THEN total_handle_ms END) AS ms_m2
FROM cx2_agent_daily
GROUP BY user_id, agent_name
HAVING COALESCE(ms_m0,0) = 0 AND COALESCE(ms_m1,0) = 0 AND COALESCE(ms_m2,0) = 0;
```

### 8.3 License waste trend (org level)

```sql
SELECT
    as_of_date,
    true_waste_count,
    zero_user_count,
    reclaimable_monthly_cost,
    cpm_full_org,
    cpm_with_usage_only
FROM cx2_org_snapshot
WHERE as_of_date >= DATE_SUB(CURDATE(), INTERVAL 365 DAY)
ORDER BY as_of_date;
```

### 8.4 Point-in-time: reproduce emailed 90d summary from September run

```sql
SELECT agent_name, market, total_handle_min, cost_per_min_total
FROM cx2_agent_snapshot
WHERE run_id = :run_id
  AND window_days = 90
ORDER BY cost_per_min_total DESC;
```

---

## 9. Implementation phases

| Phase | Scope | Effort |
|-------|-------|--------|
| **1** | DDL migration + `core/report_history.py` + ingest hooks in both CX2 scripts + `--no-store` | Small (1–2 sessions) |
| **2** | CLI query tool / CSV export; extend `cleanup_mysql_retention.py` | Small |
| **3** | Monthly 180d/365d snapshot jobs; optional Ops Demo tab | Medium |
| **4** | Azure MySQL provisioning + connection string in App Service | IT handoff |
| **5** | Extend pattern to weekly reports (direct outbound counts, spam abandon aggregates) | Medium per report |

### 9.1 Local setup (when implementing)

```powershell
cd "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
docker compose -f docker-compose.mysql.yml up -d
# Configure client.env MYSQL_* vars
python jobs/apply_report_history_schema.py   # proposed
python tools/reporting/cx2_cost_per_minute_report.py --email
```

### 9.2 Azure cutover

- Provision Azure Database for MySQL Flexible Server (or Azure SQL + `DB_TYPE=sqlserver`)
- Run schema migration on production
- App Service / WebJob uses same `core/db.py` env vars
- Mount **not required** for DB — only for `outputs/`/`logs/` files

---

## 10. Future report extension (sketch)

| Report | Daily fact table | Snapshot table |
|--------|------------------|----------------|
| Direct outbound weekly | `direct_outbound_agent_daily` | `direct_outbound_agent_week` |
| Spam abandon weekly | `spam_abandon_queue_daily` | weekly queue snapshot |
| Queue staffing gap | — | `staffing_gap_queue_snapshot` |

All reference `report_runs.run_id`.

---

## 11. Risks and mitigations

| Risk | Mitigation |
|------|------------|
| MySQL not running on PC | Non-blocking ingest; XLSX still works; Docker one-liner |
| Genesys API cannot backfill >90d in one call | Daily upsert accumulates history over time; schedule monthly 180/365 snapshot runs |
| Site/market changes retroactively | Store dimensions at ingest time; optional `effective_date` later |
| License cost changes | Store `cx2_monthly_cost` on each `report_runs` row |
| Doug ops burden | Piggyback retention on existing nightly rollup bat when enabled |

---

## 12. Decision

**Adopt Option B — extend MySQL** with tables `report_runs`, `cx2_agent_daily`, `cx2_agent_snapshot`, `cx2_zero_usage_reconciliation`, and `cx2_org_snapshot`. Ingest after each CX2 report run using idempotent upserts keyed by `run_id` and `(metric_date, user_id)`.

Defer SQLite POC unless MySQL setup blocks progress; the Docker compose file already exists and patterns are copy-paste from `build_agent_daily_metrics_sqlalchemy.py`.

---

## 13. Document history

| Date | Change |
|------|--------|
| 2026-09-09 | Initial design from repo storage audit and CX2 report column mapping |
