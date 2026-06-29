from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
PAYLOADS_DIR = PROJECT_ROOT / "payloads"
LOGS_DIR = PROJECT_ROOT / "logs"


def ensure_runtime_dirs() -> None:
    for path in (OUTPUTS_DIR, PAYLOADS_DIR, LOGS_DIR):
        path.mkdir(parents=True, exist_ok=True)
