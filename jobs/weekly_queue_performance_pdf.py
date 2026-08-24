#!/usr/bin/env python3
"""Weekly Queue Performance PDF job — generate report and copy to shared folder."""
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
from tools.reporting.direct_outbound_agents_report import previous_calendar_week
from tools.reporting.queue_performance_pdf_report import export_dir, pdf_filename

DEFAULT_QUEUE_PREFIX = "Patient Services"
TASK_NAME = "GenesysToolkit-WeeklyQueuePerformancePdf"
SCHEDULE_STATE_FILE = LOGS_DIR / "weekly_queue_performance_pdf_schedule.json"
REPORT_SCRIPT = PROJECT_ROOT / "tools" / "reporting" / "queue_performance_pdf_report.py"


def run_step(label: str, args: list[str], *, log_file: Path) -> None:
    tool_log("info", label)
    cmd = [sys.executable, *args]
    with log_file.open("a", encoding="utf-8") as handle:
        handle.write(f"\n[{datetime.now(timezone.utc).isoformat()}] {label}\n")
        handle.write(" ".join(cmd) + "\n")
        handle.flush()
        subprocess.run(cmd, cwd=PROJECT_ROOT, check=True, stdout=handle, stderr=subprocess.STDOUT)


def record_successful_run(*, dry_run: bool) -> None:
    if dry_run:
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
    parser = argparse.ArgumentParser(description="Weekly Queue Performance PDF export job")
    parser.add_argument("--queue-prefix", default=DEFAULT_QUEUE_PREFIX)
    parser.add_argument("--division", default="")
    parser.add_argument(
        "--shared-folder",
        default="",
        help="UNC or local shared folder (defaults to QUEUE_PERFORMANCE_EXPORT_DIR or outputs/queue_performance_exports/)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Generate locally only; do not update schedule state")
    args = parser.parse_args()

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_file = LOGS_DIR / f"weekly_queue_performance_pdf_{stamp}.log"
    _, start_label, end_label = previous_calendar_week()
    local_copy = OUTPUTS_DIR / pdf_filename(
        {"start_date": start_label, "end_date": end_label},
        stamp=stamp,
    )
    shared_folder = args.shared_folder or str(export_dir())

    tool_log("info", f"Previous week: {start_label} through {end_label}")
    log_file.write_text(f"weekly_queue_performance_pdf job started {stamp}\n", encoding="utf-8")

    report_args = [
        str(REPORT_SCRIPT),
        "--start-date",
        start_label,
        "--end-date",
        end_label,
        "--queue-prefix",
        args.queue_prefix,
        "--output",
        str(local_copy),
        "--shared-folder",
        shared_folder,
    ]
    if args.division:
        report_args.extend(["--division", args.division])

    run_step("Generating Queue Performance PDF", report_args, log_file=log_file)
    if not local_copy.exists():
        raise SystemExit(f"Expected PDF not created: {local_copy}")

    record_successful_run(dry_run=args.dry_run)
    tool_log("info", f"Done. Local copy: {local_copy}")
    tool_log("info", f"Shared folder target: {shared_folder}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
