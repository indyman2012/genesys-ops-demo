#!/usr/bin/env python3
"""Weekly Patient Services queue staffing gap job: generate previous week, save locally, email."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import setup_path, tool_log
from core.paths import LOGS_DIR, OUTPUTS_DIR, PROJECT_ROOT
from tools.email.config import default_email_env_file
from tools.reporting.direct_outbound_agents_report import previous_calendar_week

DEFAULT_WORK_TO = "doug.hendrickson@archwellhealth.com"
TASK_NAME = "GenesysToolkit-WeeklyQueueStaffingGap"
SCHEDULE_STATE_FILE = LOGS_DIR / "weekly_queue_staffing_gap_schedule.json"
REPORT_SCRIPT = PROJECT_ROOT / "tools" / "reporting" / "queue_staffing_gap_report.py"
MAIL_SCRIPT = PROJECT_ROOT / "tools" / "email" / "gmail_send.py"
ENV_FILE = default_email_env_file()


def weekly_email(start_label: str, end_label: str, report_path: Path, meta: dict) -> tuple[str, str]:
    subject = f"Weekly Queue Staffing Gap Report ({start_label} to {end_label})"
    body = f"""Hi Doug,

Attached is the weekly Patient Services queue staffing gap report (Phase 1).

Window: {start_label} through {end_label} (prior Mon-Sun)
Queues in scope: {meta.get('queues_in_scope', 0)}
Queues with traffic: {meta.get('queues_with_traffic', 0)}

Required FTE uses Erlang C on 30-minute intervals; Avg Active Phone FTE uses agent handle+hold+ACW time.
Staffing gap = avg required − avg active phone FTE. NCM queues excluded.

Agent Burden sheet includes ABI, excess burden hours, and burnout-adjusted queue hours.
Agents scored by ABI are sorted first (roles from config/staffing_role_expectations.csv).

See the About sheet for methodology. Queues sorted by highest average staffing gap first.

Automated Monday job from your personal PC.

— GenesysCloudToolkit
"""
    return subject, body


def load_report_meta(report_path: Path) -> dict:
    meta_path = report_path.parent / f"{report_path.stem}_meta.json"
    if not meta_path.exists():
        return {}
    return json.loads(meta_path.read_text(encoding="utf-8"))


def run_step(label: str, args: list[str], *, log_file: Path) -> None:
    tool_log("info", label)
    cmd = [sys.executable, *args]
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{datetime.now(timezone.utc).isoformat()}] {label}\n")
        handle.write(" ".join(cmd) + "\n")
        handle.flush()
        subprocess.run(cmd, cwd=PROJECT_ROOT, check=True, stdout=handle, stderr=subprocess.STDOUT)


def record_successful_run(*, dry_run_email: bool) -> None:
    if dry_run_email:
        return
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    state: dict = {}
    if SCHEDULE_STATE_FILE.exists():
        state = json.loads(SCHEDULE_STATE_FILE.read_text(encoding="utf-8-sig"))
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    state["runs_completed"] = int(state.get("runs_completed", 0)) + 1
    SCHEDULE_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Weekly queue staffing gap report + email job")
    parser.add_argument("--to", default=DEFAULT_WORK_TO)
    parser.add_argument("--env-file", default=str(ENV_FILE))
    parser.add_argument("--skip-email", action="store_true")
    parser.add_argument("--dry-run-email", action="store_true")
    args = parser.parse_args()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"weekly_queue_staffing_gap_{stamp}.log"
    _, start_label, end_label = previous_calendar_week()
    report_path = OUTPUTS_DIR / f"queue_staffing_gap_weekly_{stamp}.xlsx"

    tool_log("info", f"Previous week: {start_label} through {end_label}")
    log_file.write_text(f"weekly_queue_staffing_gap job started {stamp}\n", encoding="utf-8")

    run_step("Generating report", [str(REPORT_SCRIPT), "--output", str(report_path)], log_file=log_file)
    if not report_path.exists():
        raise SystemExit(f"Report not created: {report_path}")

    report_meta = load_report_meta(report_path)
    tool_log("ok", f"Saved {report_path}")

    if args.skip_email:
        return 0

    subject, body = weekly_email(start_label, end_label, report_path, report_meta)
    mail_args = [
        str(MAIL_SCRIPT),
        "--env-file",
        args.env_file,
        "--to",
        args.to,
        "--subject",
        subject,
        "--body",
        body,
        "--attach",
        str(report_path),
    ]
    if args.dry_run_email:
        mail_args.append("--dry-run")

    run_step("Emailing report", mail_args, log_file=log_file)
    record_successful_run(dry_run_email=args.dry_run_email)
    tool_log("ok", f"Job complete; log {log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
