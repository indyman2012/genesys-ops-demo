from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Iterator

from werkzeug.datastructures import FileStorage

from app.tool_catalog import get_tool
from app.tool_specs import ToolSpec
from core.paths import PROJECT_ROOT, ensure_runtime_dirs

UPLOADS_DIR = PROJECT_ROOT / "uploads"


def _save_upload(upload: FileStorage, tool_id: str) -> Path:
    ensure_runtime_dirs()
    UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = Path(upload.filename or "upload.csv").name
    dest = UPLOADS_DIR / f"{tool_id}_{safe_name}"
    upload.save(dest)
    return dest


def build_command(
    tool: ToolSpec,
    form: dict[str, str],
    files: dict[str, FileStorage],
    *,
    execute: bool,
) -> list[str]:
    script = PROJECT_ROOT / tool["script"]
    if not script.exists():
        raise FileNotFoundError(f"Script not found: {script}")

    cmd = [sys.executable, str(script)]
    positional: list[str] = []
    flag_values: dict[str, str] = {}
    flag_presence: set[str] = set()

    for field in tool.get("fields", []):
        name = field["name"]
        arg = field["arg"]
        field_type = field["type"]

        if field_type == "file":
            upload = files.get(name)
            if upload and upload.filename:
                path = _save_upload(upload, tool["id"])
                if field.get("positional"):
                    positional.append(str(path))
                else:
                    flag_values[arg] = str(path)
            continue

        value = (form.get(name) or field.get("default") or "").strip()

        if field_type == "checkbox":
            if form.get(name) in ("on", "true", "1", "yes"):
                flag_presence.add(arg)
            continue

        if not value:
            continue

        if field.get("positional"):
            positional.append(value)
        elif field_type == "api_select" or field_type in ("text", "email", "date", "number", "select", "textarea"):
            flag_values[arg] = value

    for arg, value in flag_values.items():
        cmd.extend([arg, value])
    for arg in sorted(flag_presence):
        cmd.append(arg)

    cmd.extend(positional)

    if tool.get("mutating"):
        flag = tool.get("apply_flag", "apply")
        if flag == "apply" and execute:
            cmd.append("--apply")
        elif flag == "execute" and execute:
            cmd.append("--execute")
        elif flag == "dry_run_inverted" and not execute:
            cmd.append("--dry-run")

    return cmd


def stream_tool_run(
    tool_id: str,
    form: dict[str, str],
    files: dict[str, FileStorage],
    *,
    execute: bool,
    demo_mode: bool = False,
) -> Iterator[str]:
    tool = get_tool(tool_id)
    if not tool:
        yield f"[ERROR] Unknown tool: {tool_id}\n"
        return

    if demo_mode:
        execute = False

    try:
        cmd = build_command(tool, form, files, execute=execute)
    except Exception as exc:
        yield f"[ERROR] {exc}\n"
        return

    mode = "DEMO DRY RUN" if demo_mode else ("EXECUTE" if execute else "DRY RUN")
    yield f"[INFO] Tool: {tool['name']} ({tool_id})\n"
    yield f"[INFO] Mode: {mode}\n"
    yield f"[INFO] Command: {' '.join(cmd)}\n"
    yield "-" * 72 + "\n"

    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")

    process = subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )

    assert process.stdout is not None
    for line in process.stdout:
        yield line

    code = process.wait()
    yield "-" * 72 + "\n"
    if code == 0:
        yield f"[OK] Finished with exit code 0\n"
    else:
        yield f"[ERROR] Finished with exit code {code}\n"
