#!/usr/bin/env python3
"""Biweekly AHT trend HTML report job: generate 4-week charts, save locally, email work inbox."""
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
MIN_DAYS_BETWEEN_RUNS = 13
TASK_NAME = "GenesysToolkit-BiweeklyAhtTrend"
SCHEDULE_STATE_FILE = LOGS_DIR / "biweekly_aht_trend_schedule.json"
REPORT_SCRIPT = PROJECT_ROOT / "tools" / "reporting" / "aht_trend_biweekly_report.py"
MAIL_SCRIPT = PROJECT_ROOT / "tools" / "email" / "gmail_send.py"
ENV_FILE = default_email_env_file()


def biweekly_email(start_label: str, end_label: str, report_path: Path, meta: dict) -> tuple[str, str]:
    subject = f"Biweekly AHT Trend Report ({start_label} to {end_label})"
    body = f"""Hi Doug,

Attached is the biweekly inbound voice AHT trend report (HTML — open in your browser).

Window: {start_label} through {end_label} (4 complete weeks)
Queues charted: {meta.get('queues_listed', 0)}
Charts: Handle / Talk / Hold / ACW weekly lines + daily ASA bars underneath

Use the queue dropdown at the top to load one queue segment at a time, then expand agents for individual trends.

Automated biweekly Monday job from your personal PC.

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


def load_schedule_state() -> dict:
    if SCHEDULE_STATE_FILE.exists():
        return json.loads(SCHEDULE_STATE_FILE.read_text(encoding="utf-8-sig"))
    return {"task_name": TASK_NAME, "min_days_between_runs": MIN_DAYS_BETWEEN_RUNS}


def save_schedule_state(state: dict) -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    SCHEDULE_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def should_run_now(state: dict, *, force: bool) -> bool:
    if force:
        return True
    last_success = state.get("last_success_at")
    if not last_success:
        return True
    last_dt = datetime.fromisoformat(last_success)
    if last_dt.tzinfo is None:
        last_dt = last_dt.replace(tzinfo=timezone.utc)
    elapsed_days = (datetime.now(timezone.utc) - last_dt).days
    min_days = int(state.get("min_days_between_runs", MIN_DAYS_BETWEEN_RUNS))
    return elapsed_days >= min_days


def record_success(*, dry_run_email: bool) -> None:
    if dry_run_email:
        return
    state = load_schedule_state()
    state["last_success_at"] = datetime.now(timezone.utc).isoformat()
    state["runs_completed"] = int(state.get("runs_completed", 0)) + 1
    save_schedule_state(state)


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Biweekly AHT trend report + email job")
    parser.add_argument("--to", default=DEFAULT_WORK_TO)
    parser.add_argument("--env-file", default=str(ENV_FILE))
    parser.add_argument("--skip-email", action="store_true")
    parser.add_argument("--dry-run-email", action="store_true")
    parser.add_argument("--force", action="store_true", help="Run even if last success was < 13 days ago")
    args = parser.parse_args()

    state = load_schedule_state()
    if not should_run_now(state, force=args.force):
        tool_log("info", "Skipping biweekly run; last success was within the minimum interval")
        return 0

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"biweekly_aht_trend_{stamp}.log"
    report_path = OUTPUTS_DIR / f"aht_trend_biweekly_{stamp}.html"

    log_file.write_text(f"biweekly_aht_trend job started {stamp}\n", encoding="utf-8")
    run_step("Generating report", [str(REPORT_SCRIPT), "--output", str(report_path)], log_file=log_file)
    if not report_path.exists():
        raise SystemExit(f"Report not created: {report_path}")

    report_meta = load_report_meta(report_path)
    tool_log("ok", f"Saved {report_path}")

    if args.skip_email:
        record_success(dry_run_email=False)
        return 0

    start_label = report_meta.get("window_start", "")
    end_label = report_meta.get("window_end", "")
    subject, body = biweekly_email(start_label, end_label, report_path, report_meta)
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
    record_success(dry_run_email=args.dry_run_email)
    tool_log("ok", f"Job complete; log {log_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
