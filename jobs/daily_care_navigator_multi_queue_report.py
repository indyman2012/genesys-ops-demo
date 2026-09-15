#!/usr/bin/env python3
"""Daily Care Navigator multi-queue report: generate snapshot, save locally, email."""
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

DEFAULT_WORK_TO = "doug.hendrickson@archwellhealth.com"
MAX_SCHEDULED_RUNS = 10
TASK_NAME = "GenesysToolkit-DailyCareNavigatorMultiQueue"
SCHEDULE_STATE_FILE = LOGS_DIR / "daily_care_navigator_multi_queue_schedule.json"
REPORT_SCRIPT = PROJECT_ROOT / "tools" / "reporting" / "care_navigator_multi_queue_report.py"
MAIL_SCRIPT = PROJECT_ROOT / "tools" / "email" / "gmail_send.py"
ENV_FILE = default_email_env_file()


def daily_email(
    report_date: str,
    report_path: Path,
    *,
    agents_matching: int,
    title_contains: str,
    min_joined_queues: int,
) -> tuple[str, str]:
    subject = f"Care Navigator Multi-Queue Report ({report_date}) — {agents_matching} agent(s)"
    body = f"""Hi Doug,

Attached is today's Care Navigator queue membership snapshot from Genesys Cloud.

Criteria:
- User state: ACTIVE
- Job title contains: {title_contains}
- Queue membership: joined=true on {min_joined_queues} or more queues

Agents matching today: {agents_matching}
Report date: {report_date}
File: {report_path.name}

This daily job is scheduled for 10 runs, then auto-retires.

— GenesysCloudToolkit
"""
    return subject, body


def load_report_meta(report_path: Path) -> dict:
    meta_path = report_path.parent / f"{report_path.stem}_meta.json"
    if not meta_path.exists():
        return {
            "agents_matching": 0,
            "title_contains": "Care Navigator",
            "min_joined_queues": 2,
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
        tool_log("ok", f"Completed {max_runs} daily runs; removed scheduled task {task_name}")
    else:
        tool_log("warn", f"Completed {max_runs} runs but could not remove scheduled task {task_name}")
    state["retired_at"] = datetime.now(timezone.utc).isoformat()
    save_schedule_state(state)


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Daily Care Navigator multi-queue report + email")
    parser.add_argument("--to", default=DEFAULT_WORK_TO)
    parser.add_argument("--env-file", default=str(ENV_FILE))
    parser.add_argument("--skip-email", action="store_true", help="Generate report only")
    parser.add_argument("--dry-run-email", action="store_true", help="Generate report; preview email only")
    args = parser.parse_args()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    report_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"daily_care_navigator_multi_queue_{stamp}.log"
    report_path = OUTPUTS_DIR / f"care_navigator_multi_queue_{stamp}.xlsx"

    log_file.write_text(f"daily_care_navigator_multi_queue job started {stamp}\n", encoding="utf-8")

    run_step(
        "Generating report",
        [str(REPORT_SCRIPT), "--output", str(report_path)],
        log_file=log_file,
    )
    if not report_path.exists():
        raise SystemExit(f"Report not created: {report_path}")

    report_meta = load_report_meta(report_path)
    tool_log("ok", f"Saved {report_path} ({report_meta.get('agents_matching', 0)} agents)")

    if args.skip_email:
        return 0

    subject, body = daily_email(
        report_date,
        report_path,
        agents_matching=int(report_meta.get("agents_matching", 0)),
        title_contains=str(report_meta.get("title_contains", "Care Navigator")),
        min_joined_queues=int(report_meta.get("min_joined_queues", 2)),
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
