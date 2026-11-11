#!/usr/bin/env python3
"""Stage curated GenesysToolkit web app tree for Azure deployment."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import setup_path, tool_log
from deploy.webapp_bundle import stage_webapp_bundle

setup_path()


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage Azure web app bundle directory.")
    parser.add_argument(
        "--output-dir",
        default="azure-bundle",
        help="Directory to populate (default: azure-bundle)",
    )
    args = parser.parse_args()
    out = stage_webapp_bundle(Path(args.output_dir))
    tool_log("ok", f"Staged web app bundle at {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
