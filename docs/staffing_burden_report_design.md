# Genesys Queue Staffing & Agent Burden Metrics

**Organization:** ArchWell Health — Genesys Cloud (usw2)  
**Status:** Phase 1 implemented (queue staffing gap weekly report)  
**Last updated:** 2026-07-16  
**Toolkit repo:** `GenesysCloudToolkit` (product name: **GenesysToolkit**)

---

## Document purpose

This document consolidates:

1. The original staffing and agent burden design
2. Feasibility review against Genesys APIs and existing toolkit assets
3. **Locked decisions** from design review sessions (2026-07-16)

Intended for operational leadership review and as a build spec for Phase 1 reporting.

---

## Goal

Create reporting that answers two distinct questions:

1. **Do we have enough staffing for each queue?**
2. **Are individual employees carrying more phone work than their role is intended to handle?**

These metrics should support staffing recommendations while identifying employees who are consistently overburdened.

**This is not a performance scorecard.** Metrics are framed for staffing, burnout detection, and operational intervention — not agent ranking.

---

## Locked scope decisions (2026-07-16)

| Decision | Choice |
|----------|--------|
| NCM queues | **Excluded from v1** — different function, metrics, and wrap-up model (`NCM_*`, `CM InQueue`) |
| Multi-queue burden | **Burnout factor** — per-queue time summed without normalizing to 1.0 per agent-hour (see §2.2) |
| Qualifying agent | `joined = true` + queue-eligible presence/routing (see §2.1) |
| ACE allocation (queue side) | Required FTE allocated proportionally to qualifying agents by available seconds |
| ACE / burden (agent side) | Each in-scope queue credited independently; multi-queue overlap counts as additive strain |
| Primary pilot scope | Patient Services queues first; Sales/Broker and specialty queues in-scope but secondary |
| Interval granularity | 15 or 30 minutes (recommend **30 min** for Phase 1 API volume) |

---

# Part A — Original metric definitions

## 1. Queue FTE requirement

Calculate required staffing by queue using interval-based data (15 or 30 minute intervals).

### Required inputs

- Offered interactions
- Answered interactions
- Abandons
- Average Talk Time
- Average Hold Time
- Average After Call Work (ACW)
- Service Level target
- Average Speed of Answer
- Agents available during the interval

### Core calculations

**Average Handle Time**

```
AHT = Talk + Hold + ACW
```

**Workload (Erlangs)**

```
Workload = (Offered Contacts × AHT) / Interval Length
```

**Required staffed FTE**

Use an Erlang C (or equivalent staffing) calculation to determine the number of staffed agents required to achieve the desired service level.

Apply shrinkage separately:

- PTO
- Breaks
- Meetings
- Training
- Unplanned absence
- Other non-queue work

**Final recommendation**

```
Required FTE = max(Service Level Requirement, Minimum Coverage Requirement)
```

> **Review note:** Minimum coverage requirement is referenced but not yet defined. Recommend: at least 1 qualifying joined agent during published business hours per center, regardless of Erlang output.

---

## 2. Agent Coverage Equivalent (ACE)

Measure how much required staffing an individual agent actually supplied toward queue coverage.

**This is not a performance metric.**

For each interval:

1. Determine required staffed FTE for the queue.
2. Determine qualifying available agents.
3. Allocate required staffing across those agents based on available time (proportional).
4. Sum each agent's contribution across the reporting period.

### Outputs

- FTE-hours covered
- Coverage Equivalent
- Average staffing burden while active

---

## 3. Front Desk phone utilization

**Expected business rule**

> Front Desk employees should spend approximately **20% of their workday on phone activities**.

### Expected phone hours

```
Expected Phone Hours = Logged In Hours × 20%
```

### Phone controlled time

**Include:**

- Interacting
- Hold
- ACW
- Queue outbound calls
- Optional: Alerting time

**Exclude:**

- Break
- Meal
- Training
- Meetings
- Offline
- Non-phone work

### Phone utilization

```
Phone Utilization = Phone Controlled Time / Logged In Time
```

> **Review note:** The 20% rule applies to Front Desk / MA-type roles. Care Navigators and other phone-primary roles need a separate expected-% mapping (see §4.3). Applying 20% uniformly will flag clinical phone staff as permanently overloaded.

---

## 4. Phone allocation utilization

Compare actual phone work against expected phone work.

```
Phone Allocation Utilization = Actual Phone Hours / Expected Phone Hours
```

### Suggested interpretation

| Value | Meaning |
|-------|---------|
| < 100% | Below expected phone usage |
| 100% | At target |
| 125% | Elevated |
| 150%+ | Sustained overload |
| 200%+ | Severe staffing issue |

---

## 5. Excess burden hours

```
Excess Burden Hours = Actual Phone Hours − Expected Phone Hours
```

Represents front-desk capacity lost to phone demand.

**Example**

- Logged In: 35 hrs
- Expected Phone: 7 hrs
- Actual Phone: 11 hrs
- **Excess Burden = 4 hrs**

---

## 6. Agent Burden Index (ABI)

Composite operational metric.

```
ABI = Phone Allocation Utilization × Average Staffing Burden
```

### Suggested ranges (calibrate against historical data before enforcement)

| ABI | Interpretation |
|-----|----------------|
| < 0.80 | Light burden |
| 0.80–1.10 | Normal |
| 1.10–1.35 | Elevated |
| 1.35–1.60 | Sustained strain |
| > 1.60 | Operational intervention recommended |
| > 2.00 | Critical overload |

---

## 7. Recommended reports

### Queue dashboard

- Queue
- Offered contacts
- AHT
- Service level
- Required FTE
- Actual staffed FTE
- Staffing gap
- Average occupancy

### Agent dashboard

- Agent
- Logged in hours
- Phone controlled hours
- Phone utilization %
- Expected phone hours
- Phone allocation utilization
- Excess burden hours
- FTE-hours covered
- Coverage Equivalent (ACE)
- Average staffing burden
- Agent Burden Index (ABI)
- **Queues active (count)** — added in review
- **Multi-queue interval count / %** — added in review
- **Qualifying hours (summed, burnout-adjusted)** — added in review

### Trending

- Weekly averages
- Rolling 4-week averages
- Days above 25% phone utilization
- Days above 30% phone utilization
- Consecutive elevated weeks
- Peak interval burden
- Queue staffing gap trends

---

## 8. Original design principles

- Perform calculations at 15 or 30 minute intervals.
- Calculate queue demand separately from agent contribution.
- Support shared staffing pools without double-counting agents.
- Distinguish staffing shortages from individual performance.
- Make every metric explainable to operational leadership.
- Optimize for identifying staffing needs, recurring overload, and operational bottlenecks rather than ranking employees.

> **Review change:** The original principle "without double-counting agents" is **modified** for agent-side burden metrics. See §2.2 (burnout factor). Queue-side demand calculations remain single-count per queue.

---

# Part B — Reviewed design (locked changes)

## 2.1 Qualifying agent logic (LOCKED)

An agent counts as qualifying for a given **in-scope queue** in a given **interval** when **all** of the following are true:

| # | Criterion | Source |
|---|-----------|--------|
| 1 | `joined = true` on that queue | `GET /api/v2/routing/queues/{queueId}/members` |
| 2 | Queue-eligible presence | Not Away, On Break, Meal, Training, Busy, DND, or Offline |
| 3 | Routing-eligible | Available for queue work (not in a blocking routing state) |

**Important distinctions**

| Use case | Member filter |
|----------|---------------|
| In-app queue alerts (separate initiative) | `joined = true` |
| Staffing / ACE / burden (this report) | `joined = true` + presence + routing |
| Center admin email discovery | Title match on all members (not joined) |

**Alignment with existing toolkit**

- Presence filtering pattern: `app/agent_workspace/live_queue_panels.py`
- Joined scan pattern: `tools/reporting/care_navigator_multi_queue_report.py`, `tools/reporting/direct_outbound_agents_report.py`
- Queue alert design: `docs/queue_alert_monitor_design.md`

---

## 2.2 Multi-queue burden — burnout factor (LOCKED)

### Policy

From a **staffing and burnout** viewpoint, multi-queue activity is **additive**, not fractional.

> If an employee is qualifying-active for one hour on Queue A and one hour on Queue B, **burden = 2**, not 1.

When the same clock interval overlaps (agent qualifying on two in-scope queues simultaneously), **each queue receives full credit** for that interval. Do not divide by the number of queues joined.

### Rationale

- Being joined on multiple queues creates cognitive load, split attention, and routing competition even when only one call is active.
- Aligns with observed operational pain: Care Navigators joined on 2+ queues (53 agents on first compliance snapshot).
- Surfaces staffing risk that proportional allocation would hide.

### Agent-side formulas

```
qualifying_hours_summed = sum(qualifying_seconds per in-scope queue) / 3600

average_staffing_burden = qualifying_hours_summed / logged_in_hours
```

`average_staffing_burden` **can exceed 1.0** by design.

### Queue-side ACE allocation (unchanged)

Within a single queue interval, required FTE is still allocated **proportionally** across qualifying agents based on each agent's available seconds in that queue:

```
agent_ACE_share_in_queue = required_FTE_queue × (agent_available_seconds_in_queue / sum_of_all_qualifying_available_seconds_in_queue)
```

An agent's total ACE for the period is the **sum across in-scope queues** — each queue ledger is independent.

### Example

| Scenario | Proportional model | Burnout factor (locked) |
|----------|-------------------|-------------------------|
| 1 hr joined on PS-Eastlake only | Burden 1.0 | Burden 1.0 |
| 1 hr joined on PS-Eastlake + PS-Westlake (same hour) | Burden 1.0 (0.5 + 0.5) | **Burden 2.0** |
| 1 hr on PS-Eastlake, then 1 hr on PS-Westlake | Burden 1.0 or 2.0 depending on split | **Burden 2.0** |

### Org-level implication

Summed ACE / qualifying hours across all agents **may exceed** total org clock hours when many employees are multi-joined. This is intentional — the report measures **strain**, not conservation of labor hours. Leadership must receive the methodology footnote (§6).

---

## 2.3 NCM exclusion (LOCKED)

### Out of scope for v1

- All queues with names starting with `NCM_` (~108 queues)
- `CM InQueue` flow metrics
- `NCM_*` wrap-up taxonomy
- NCM-specific ACE, burden, ABI, and Front Desk 20% comparisons

### In scope for v1

| Family | Approx. count | In-queue flow |
|--------|---------------|---------------|
| Patient Services | ~102 | `PS In-Queue` |
| Sales | ~22 | `Sales In-Queue`, `Broker In-Queue` |
| Pharmacy | 2 | `Pharmacy In-Queue` |
| Cardiology | 1 | `Cardiology Inqueue` |
| Member Services / Retention | 2 | `InQueue VM and Callback`, `Member Retention with VM` |
| Other non-NCM specialty | varies | Per queue |

### Cross-family agents

If an agent is joined on **PS + NCM**:

- Only PS (and other in-scope) qualifying time counts toward burden and ACE.
- NCM intervals are ignored entirely — not partial credit, not error.

If an agent is joined on **two in-scope queues** (e.g., two PS centers, or PS + Sales):

- Full burnout factor applies across in-scope queues only.

### Future

NCM may receive a separate analysis workbook with clinical-appropriate metrics and expectations. Do not merge into v1 staffing reports.

---

## 2.4 Role-to-expectation mapping (OPEN — required before Front Desk metrics)

The 20% phone expectation is not universal. Phase 1 should include a configurable role map:

| Column | Example |
|--------|---------|
| `title_contains` | `Front Desk`, `Medical Assistant`, `Care Navigator` |
| `expected_phone_pct` | `0.20`, `0.20`, `0.50` |
| `include_in_burden_report` | `true` / `false` |
| `notes` | Free text |

**Suggested file:** `config/staffing_role_expectations.csv` (template to be created at build time)

Agents whose titles do not match any row: exclude from Front Desk allocation metrics but may still appear in ACE / staffing gap views.

---

# Part C — Feasibility assessment

## Overall verdict

| Dimension | Assessment |
|-----------|------------|
| Feasibility | **Yes** — phased build with definitions above |
| Effectiveness | **High** for ops leadership and center admins |
| Hardest parts | Burnout factor communication; role mapping; 250-queue roll-up strategy |
| Best existing foundation | AHT biweekly report, queue daily metrics, live queue panels, Care Navigator compliance |

## Data source mapping

| Input | Genesys API | Toolkit status |
|-------|-------------|----------------|
| Offered / answered / abandons | Analytics aggregates: `nOffered`, `tAnswered`, `tAbandon` | Partial |
| Talk / hold / ACW | `tTalk`, `tHeld`, `tAcw` | **Built** — `aht_trend_biweekly_report.py` |
| Service level / ASA | `oServiceLevel`, ASA metrics | Live panels + queue metrics builder |
| Queue members joined | `/routing/queues/{id}/members` | **Built** — multiple tools |
| Presence | `/users/{id}/presences/purecloud` | **Built** — `live_queue_panels.py` |
| Logged-in / on-queue time | User analytics aggregates | **Gap** — new queries needed |
| Shrinkage | Not in Genesys | **Config CSV** — manual assumptions |

## Recommended roll-up for leadership

250 queues is correct at the data layer but overwhelming in reports. Publish at:

1. **Center + queue family** (e.g., PS - CLE - Eastlake)
2. **Drill-down** to individual queue on demand

## Relationship to other initiatives

| Initiative | Relationship |
|------------|--------------|
| Queue alert monitor (`docs/queue_alert_monitor_design.md`) | Complementary — alerts on live WAIT/ABANDON; this report explains structural staffing gaps |
| Care Navigator multi-queue report | Complementary — daily compliance snapshot; burnout factor quantifies the strain |
| Weekly pain-point reports (spam, callback, repeat caller, ACW timeout) | Explanatory context for why burden and staffing gap spike |
| Agent Workspace pilot | Future delivery surface for supervisor/agent views |

---

# Part D — Implementation phases

## Phase 1 — Queue staffing gap (PS centers)

- 30-minute interval aggregates for PS queues
- Erlang C required FTE per queue / center
- Actual staffed FTE = average qualifying joined agents per interval
- Staffing gap = required − actual
- Weekly Excel + optional dashboard tab
- **No ABI yet**

## Phase 2 — Front Desk burden

- Role expectation CSV
- Phone controlled time vs expected
- Excess burden hours
- Pilot on 2–3 centers

## Phase 3 — ACE with burnout factor

- Per-queue ACE ledger with proportional allocation within queue
- Agent-side summing across in-scope queues
- Multi-queue interval flags
- Validate against known overloaded agents manually

## Phase 4 — ABI + trending

- Composite index after 4–8 weeks baseline
- Calibrate thresholds from historical data
- Rolling 4-week averages and consecutive elevated week alerts

---

# Part E — Report methodology disclosure (required on all outputs)

Include this section verbatim (or equivalent) on every report export:

---

### Multi-queue burden (burnout factor)

This report measures staffing load from an **operational burden** perspective, not agent efficiency or performance.

When an employee is qualifying-active on multiple in-scope queues, **each queue receives full credit** for that time. One hour active on two queues is reported as **burden 2**, not 1. This intentionally surfaces multi-queue membership as a burnout and staffing risk.

Summed qualifying hours across agents may exceed total clock hours when many employees are multi-joined. This is expected and reflects organizational strain, not a data error.

**NCM queues are excluded** from this analysis. NCM operates under a different clinical care model and metric set.

**Qualifying agent** means `joined = true` on the queue plus queue-eligible presence and routing status during the interval.

---

# Part F — Open items for next review

| # | Item | Owner / action |
|---|------|----------------|
| 1 | Define minimum coverage requirement per center | Operations / WFM |
| 2 | Service level target per queue family (PS, Sales, etc.) | Operations |
| 3 | Shrinkage assumptions (PTO, breaks, training %) | WFM |
| 4 | Role-to-expectation CSV contents | HR / Operations titles |
| 5 | Include alerting time in phone controlled time? | Operations |
| 6 | Callback and direct-outbound treatment in phone hours | Align with existing weekly reports |
| 7 | ABI threshold calibration window | After Phase 3 baseline |

---

# Part G — GenesysCloudToolkit build targets (when approved)

| Artifact | Path (proposed) |
|----------|-----------------|
| Design doc (this file) | `docs/staffing_burden_report_design.md` |
| Role expectations template | `templates_csv/staffing_role_expectations_template.csv` |
| Shrinkage config template | `templates_csv/staffing_shrinkage_template.csv` |
| Phase 1 report script | `tools/reporting/queue_staffing_gap_report.py` ✅ |
| Phase 1 weekly job | `jobs/weekly_queue_staffing_gap_report.py` ✅ |
| Phase 1 launcher | `run_weekly_queue_staffing_gap.bat` |
| Phase 1 task register | `scripts/register_weekly_queue_staffing_gap_task.ps1` |
| Erlang calculator | `core/erlang.py` ✅ |
| Staffing defaults config | `config/staffing_queue_defaults.csv` ✅ |
| Phase 3 ACE / burden script | `tools/reporting/agent_burden_report.py` |

**Existing code to reuse**

- `tools/reporting/aht_trend_biweekly_report.py` — metric parsing, queue filters
- `jobs/build_queue_daily_metrics_sqlalchemy.py` — interval rollups to MySQL
- `tools/reporting/care_navigator_multi_queue_report.py` — joined scan, title filter
- `app/agent_workspace/live_queue_panels.py` — presence eligibility

---

## Revision history

| Date | Change |
|------|--------|
| 2026-07-16 | Initial design review; feasibility assessment; locked qualifying agent logic, proportional ACE within queue, NCM exclusion, burnout factor for multi-queue burden |
| 2026-07-16 | Compiled into single document for external review |
