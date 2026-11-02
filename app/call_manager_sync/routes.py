from __future__ import annotations

from typing import Any

from flask import jsonify, request

from app.ops_auth import require_queue_performance_access
from core.cli import create_client, tool_log
from core.datatables import datatable_name_from_env, find_datatable_by_name
from core.genesys_client import GenesysError


def _auth_block():
    return require_queue_performance_access()


def _parse_upload() -> tuple[list[dict[str, str]] | None, Any]:
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return None, (jsonify({"error": "Upload an Excel file (.xlsx)"}), 400)
    from tools.queues.ncm_call_manager_sync_lib import parse_ncm_roster_workbook

    try:
        rows = parse_ncm_roster_workbook(upload.stream)
    except Exception as exc:
        return None, (jsonify({"error": f"Could not read Excel file: {exc}"}), 400)
    if not rows:
        return None, (jsonify({"error": "No roster rows found (Email, Queue Name, Phone Number)"}), 400)
    return rows, None


def setup_api():
    blocked = _auth_block()
    if blocked:
        return blocked
    name = datatable_name_from_env()
    try:
        client = create_client(log=tool_log)
        table = find_datatable_by_name(client, name)
        return jsonify(
            {
                "datatable_name": name,
                "datatable_id": table.get("id"),
                "message": "Upload Excel with Email Address, Queue Name, and Phone Number columns.",
            }
        )
    except GenesysError as exc:
        return jsonify({"error": str(exc), "datatable_name": name}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def sync_api():
    blocked = _auth_block()
    if blocked:
        return blocked
    roster, err = _parse_upload()
    if err:
        return err
    apply = request.form.get("apply") == "1"
    try:
        from tools.queues.ncm_call_manager_sync_lib import run_ncm_call_manager_sync

        client = create_client(log=tool_log)
        payload = run_ncm_call_manager_sync(client, roster, apply=apply)
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
