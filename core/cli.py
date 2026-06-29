from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Callable

from core.genesys_client import GenesysClient, safe_str
from core.paths import OUTPUTS_DIR, PAYLOADS_DIR, PROJECT_ROOT, ensure_runtime_dirs


LogFn = Callable[[str, str], None]

_LEVEL_PREFIX = {
    "ok": "[OK]",
    "warn": "[WARN]",
    "error": "[ERROR]",
    "dry": "[DRY RUN]",
    "info": "[INFO]",
}


def setup_path() -> None:
    root = str(PROJECT_ROOT)
    if root not in sys.path:
        sys.path.insert(0, root)


def tool_log(level: str, message: str) -> None:
    prefix = _LEVEL_PREFIX.get(level, "[INFO]")
    print(f"{prefix} {message}", flush=True)


def add_apply_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Default is dry run (preview only).",
    )


def add_env_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--env-file",
        default=None,
        help=f"Path to client.env (default: {PROJECT_ROOT / 'client.env'})",
    )


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path or not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as handle:
        return [{k: safe_str(v).strip() for k, v in row.items()} for row in csv.DictReader(handle)]


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(name: str, item: Any, *, subdir: str = "payloads") -> Path:
    ensure_runtime_dirs()
    base = PAYLOADS_DIR if subdir == "payloads" else OUTPUTS_DIR
    out = base / name
    with out.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(item, ensure_ascii=False) + "\n")
    return out


def output_path(name: str) -> Path:
    ensure_runtime_dirs()
    return OUTPUTS_DIR / name


def create_client(log: LogFn | None = None, env_file: str | None = None) -> GenesysClient:
    setup_path()
    ensure_runtime_dirs()
    env_path = Path(env_file) if env_file else None
    client = GenesysClient(PROJECT_ROOT, log=log or tool_log, env_path=env_path)
    client.authenticate()
    return client


def print_mode_banner(title: str, *, apply: bool, **details: str) -> None:
    print("=" * 72)
    print(title)
    print(f"Mode: {'APPLY' if apply else 'DRY RUN'}")
    for key, value in details.items():
        print(f"{key}: {value}")
    print("=" * 72)
