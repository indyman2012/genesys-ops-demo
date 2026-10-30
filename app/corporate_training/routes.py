from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request, send_from_directory

from app.ops_auth import require_queue_performance_access
from core.cli import create_client, tool_log
from core.paths import PROJECT_ROOT
from tools.queues.corporate_training_roster_lib import (
    list_corporate_training_queues,
    parse_names_from_workbook,
    run_return_from_training,
    run_start_training,
)

corporate_training_bp = Blueprint("corporate_training", __name__)
STATIC_DIR = PROJECT_ROOT / "static" / "corporate_training"


@corporate_training_bp.route("/")
def index_page():
    return send_from_directory(STATIC_DIR, "index.html")


@corporate_training_bp.route("/assets/<path:filename>")
def static_assets(filename: str):
    return send_from_directory(STATIC_DIR, filename)


def _auth_block():
    return require_queue_performance_access()


def _parse_upload() -> tuple[list[str] | None, Any]:
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return None, (jsonify({"error": "Upload an Excel file (.xlsx)"}), 400)
    try:
        names = parse_names_from_workbook(upload.stream)
    except Exception as exc:
        return None, (jsonify({"error": f"Could not read Excel file: {exc}"}), 400)
    if not names:
        return None, (jsonify({"error": "No names found in the spreadsheet"}), 400)
    return names, None


@corporate_training_bp.route("/api/setup")
def setup_api():
    blocked = _auth_block()
    if blocked:
        return blocked
    try:
        client = create_client(log=tool_log)
        training = list_corporate_training_queues(client)
        return jsonify(
            {
                "training_queues": [q.get("name") for q in training],
                "training_queue_count": len(training),
            }
        )
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@corporate_training_bp.route("/api/start-training", methods=["POST"])
def start_training_api():
    blocked = _auth_block()
    if blocked:
        return blocked
    names, err = _parse_upload()
    if err:
        return err
    apply = request.form.get("apply") == "1"
    try:
        client = create_client(log=tool_log)
        payload = run_start_training(client, names, apply=apply)
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@corporate_training_bp.route("/api/return-from-training", methods=["POST"])
def return_from_training_api():
    blocked = _auth_block()
    if blocked:
        return blocked
    names, err = _parse_upload()
    if err:
        return err
    apply = request.form.get("apply") == "1"
    try:
        client = create_client(log=tool_log)
        payload = run_return_from_training(client, names, apply=apply)
        return jsonify(payload)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
