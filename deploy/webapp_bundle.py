"""Curated GenesysToolkit web app bundle (hub launcher + ops console + mini-apps).

Used by Azure GitHub Actions deploy and scripts/package_azure_app_service.py.
Secrets are never included — configure App Service application settings in Azure.
"""
from __future__ import annotations

import shutil
import zipfile
from pathlib import Path

# Repo root (parent of deploy/)
ROOT = Path(__file__).resolve().parents[1]

# Top-level paths copied into the bundle (files or directories).
WEBAPP_INCLUDE_TOP: tuple[str, ...] = (
    "app",
    "core",
    "tools",
    "templates",
    "templates_csv",
    "static",
    "genesys_toolkit_app.py",
    "requirements.txt",
    "startup.sh",
    "docs/azure_app_service_handoff_20261022.md",
)

SKIP_DIR_NAMES = frozenset({
    "__pycache__",
    ".git",
    ".pytest_cache",
    "node_modules",
    "outputs",
    "logs",
    "uploads",
    "archive",
    "jobs",
    "tests",
    "dashboards",
    "scripts",
    "agent-transcripts",
    "deploy",
    ".github",
})

# Skip noisy artifacts under tools/ but keep package structure.
SKIP_EXTENSIONS = frozenset({".pyc", ".pyo", ".log"})


def should_skip(path: Path) -> bool:
    try:
        rel = path.relative_to(ROOT)
    except ValueError:
        return True
    for part in rel.parts:
        if part in SKIP_DIR_NAMES:
            return True
    if path.suffix.lower() in SKIP_EXTENSIONS:
        return True
    if path.name.endswith(".env"):
        return True
    return False


def _copy_tree(src: Path, dest: Path) -> None:
    if src.is_file():
        if should_skip(src):
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        return
    if not src.is_dir():
        return
    for path in src.rglob("*"):
        if path.is_dir():
            continue
        if should_skip(path):
            continue
        rel = path.relative_to(src)
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def stage_webapp_bundle(output_dir: Path) -> Path:
    """Copy curated web app files into output_dir (deployment root)."""
    output_dir = output_dir.resolve()
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    missing: list[str] = []
    for rel in WEBAPP_INCLUDE_TOP:
        src = ROOT / rel
        dest = output_dir / rel
        if not src.exists():
            missing.append(rel)
            continue
        _copy_tree(src, dest)

    if missing:
        raise FileNotFoundError(f"Bundle is missing required paths: {', '.join(missing)}")

    return output_dir


def write_webapp_zip(output_zip: Path) -> Path:
    output_zip = output_zip.resolve()
    output_zip.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output_zip, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in WEBAPP_INCLUDE_TOP:
            base = ROOT / rel
            if not base.exists():
                continue
            if base.is_file():
                if not should_skip(base):
                    zf.write(base, rel.replace("\\", "/"))
                continue
            for path in base.rglob("*"):
                if path.is_dir() or should_skip(path):
                    continue
                arc = path.relative_to(ROOT).as_posix()
                zf.write(path, arc)
    return output_zip
