#!/usr/bin/env python3
"""Weekly direct-outbound report job: generate previous week, save locally, email work inbox."""
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
DEFAULT_MIN_COUNT = 30
MAX_SCHEDULED_RUNS = 5
TASK_NAME = "GenesysToolkit-WeeklyDirectOutbound"
SCHEDULE_STATE_FILE = LOGS_DIR / "weekly_direct_outbound_schedule.json"
REPORT_SCRIPT = PROJECT_ROOT / "tools" / "reporting" / "direct_outbound_agents_report.py"
MAIL_SCRIPT = PROJECT_ROOT / "tools" / "email" / "gmail_send.py"
ENV_FILE = default_email_env_file()


def weekly_email(
    start_label: str,
    end_label: str,
    report_path: Path,
    *,
    applied_threshold: int,
    threshold_mode: str,
    threshold_policy: str,
    agents_matching: int,
    all_agents_count: int,
) -> tuple[str, str]:
    subject = f"Weekly Direct Outbound Agents Report ({start_label} to {end_label})"
    body = f"""Hi Doug,

Attached is the weekly direct-outbound agents report from Genesys Cloud.

Window: {start_label} through {end_label} (prior Mon-Sun)
Threshold policy: {threshold_policy}
Applied this week: more than {applied_threshold} direct outbound calls per agent ({threshold_mode})
Agents on threshold sheet: {agents_matching}
All Agents sheet: {all_agents_count} agents with one or more direct outbound calls
Purpose: educational coaching for direct dials that should have been placed on behalf of a queue

File: {report_path.name}

Automated Monday job from your personal PC.

— GenesysCloudToolkit
"""
    return subject, body


def load_report_meta(report_path: Path) -> dict:
    meta_path = report_path.parent / f"{report_path.stem}_meta.json"
    if not meta_path.exists():
        return {
            "applied_threshold": DEFAULT_MIN_COUNT,
            "threshold_mode": "unknown",
            "threshold_policy": f">{DEFAULT_MIN_COUNT}",
            "agents_matching": 0,
        }
    return json.loads(meta_path.read_text(encoding="utf-8"))


def run_step(label: str, args: list[str], *, log_file: Path) -> None:
    tool_log("info", label)
    cmd = [sys.executable, *args]
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{datetime.now(timezone.utc).isoformat()}] {label}\n")
        handle.write(" ".join(cmd) + "\n")
        handle.flush()
        subprocess.run(cmd, cwd=PROJECT_ROOT, check=True, stdout=handle, stderr=subprocess.STDOUT)


def load_schedule_state() -> dict:
    if SCHEDULE_STATE_FILE.exists():
        return json.loads(SCHEDULE_STATE_FILE.read_text(encoding="utf-8-sig"))
    return {
        "task_name": TASK_NAME,
        "max_runs": MAX_SCHEDULED_RUNS,
        "runs_completed": 0,
    }


def save_schedule_state(state: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    SCHEDULE_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def unregister_scheduled_task(task_name: str) -> bool:
    result = subprocess.run(
        ["schtasks", "/Delete", "/TN", task_name, "/F"],
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    return result.returncode == 0


def record_successful_run(*, dry_run_email: bool) -> None:
    if dry_run_email:
        return
    state = load_schedule_state()
    state["runs_completed"] = int(state.get("runs_completed", 0)) + 1
    state["last_run_at"] = datetime.now(timezone.utc).isoformat()
    max_runs = int(state.get("max_runs", MAX_SCHEDULED_RUNS))
    save_schedule_state(state)
    tool_log("info", f"Scheduled run {state['runs_completed']} of {max_runs}")
    if state["runs_completed"] < max_runs:
        return
    task_name = str(state.get("task_name") or TASK_NAME)
    if unregister_scheduled_task(task_name):
        tool_log("ok", f"Completed {max_runs} weekly runs; removed scheduled task {task_name}")
    else:
        tool_log("warn", f"Completed {max_runs} runs but could not remove scheduled task {task_name}")
    state["retired_at"] = datetime.now(timezone.utc).isoformat()
    save_schedule_state(state)


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Weekly direct outbound report + email job")
    parser.add_argument("--to", default=DEFAULT_WORK_TO)
    parser.add_argument("--env-file", default=str(ENV_FILE))
    parser.add_argument("--skip-email", action="store_true", help="Generate report only")
    parser.add_argument("--dry-run-email", action="store_true", help="Generate report; preview email only")
    args = parser.parse_args()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"weekly_direct_outbound_{stamp}.log"
    _, start_label, end_label = previous_calendar_week()
    report_path = OUTPUTS_DIR / f"direct_outbound_agents_weekly_{stamp}.xlsx"

    tool_log("info", f"Previous week: {start_label} through {end_label}")
    log_file.write_text(f"weekly_direct_outbound job started {stamp}\n", encoding="utf-8")

    run_step(
        "Generating report",
        [
            str(REPORT_SCRIPT),
            "--period",
            "previous-week",
            "--adaptive-threshold",
            "--output",
            str(report_path),
        ],
        log_file=log_file,
    )
    if not report_path.exists():
        raise SystemExit(f"Report not created: {report_path}")

    report_meta = load_report_meta(report_path)
    tool_log(
        "ok",
        f"Saved {report_path} ({report_meta.get('agents_matching', 0)} agents at >{report_meta.get('applied_threshold', DEFAULT_MIN_COUNT)})",
    )

    if args.skip_email:
        return 0

    subject, body = weekly_email(
        start_label,
        end_label,
        report_path,
        applied_threshold=int(report_meta.get("applied_threshold", DEFAULT_MIN_COUNT)),
        threshold_mode=str(report_meta.get("threshold_mode", "unknown")),
        threshold_policy=str(report_meta.get("threshold_policy", f">{DEFAULT_MIN_COUNT}")),
        agents_matching=int(report_meta.get("agents_matching", 0)),
        all_agents_count=int(report_meta.get("all_agents_count", 0)),
    )
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
