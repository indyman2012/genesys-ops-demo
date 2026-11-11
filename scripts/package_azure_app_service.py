#!/usr/bin/env python3
"""Build Azure App Service deployment zip (curated web app + optional env file for local reference)."""
from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.cli import output_path, setup_path, tool_log
from deploy.webapp_bundle import stage_webapp_bundle

setup_path()

ROOT = Path(__file__).resolve().parents[1]


def zip_tree(source: Path, dest_zip: Path) -> None:
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in source.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(source).as_posix())


def main() -> int:
    parser = argparse.ArgumentParser(description="Package GenesysToolkit for Azure App Service.")
    parser.add_argument(
        "--env-file",
        help="Optional: copy env export into zip under config/ (production should use App Service settings)",
    )
    parser.add_argument("--output", help="Output zip path")
    args = parser.parse_args()

    stamp = datetime.now().strftime("%Y%m%d")
    out = Path(args.output) if args.output else output_path(f"GenesysToolkit_AzureAppService_{stamp}.zip")

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp) / "bundle"
        stage_webapp_bundle(staging)

        if args.env_file:
            env_src = Path(args.env_file)
            if not env_src.is_file():
                raise SystemExit(f"Env file not found: {env_src}")
            config_dir = staging / "config"
            config_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(env_src, config_dir / "genesys-ops-demo-3.env")
            # Also keep a local copy for Doug's packaging workflow
            local_config = ROOT / "config"
            local_config.mkdir(exist_ok=True)
            shutil.copy2(env_src, local_config / "genesys-ops-demo-3.env")

        zip_tree(staging, out)

    tool_log("ok", f"Created {out} ({out.stat().st_size // 1024} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
