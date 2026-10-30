from __future__ import annotations

import os

from flask import Blueprint, Response, jsonify, render_template, request, send_from_directory

from app.ops_auth import (
    queue_performance_auth_enabled,
    require_queue_performance_access,
)
from app.not_responding import fetch_not_responding_agents
from app.queue_performance import fetch_queue_detail, fetch_queue_performance, parse_filters
from app.tool_catalog import get_tool, tools_by_category
from app.tool_runner import stream_tool_run
from core.cli import create_client, tool_log
from core.config import agent_credentials_present, credentials_env_probe, credential_source, missing_admin_credentials_message, client_credentials_present
from core.demo_mode import is_demo_mode
from core.genesys_client import safe_str
from core.paths import OUTPUTS_DIR, PROJECT_ROOT, ensure_runtime_dirs

ops_bp = Blueprint("ops", __name__)


def _demo_mode() -> bool:
    from flask import current_app
    return bool(current_app.config.get("DEMO_MODE")) or is_demo_mode()


@ops_bp.route("/")
def index():
    demo = _demo_mode()
    return render_template(
        "ops/index.html",
        categories=tools_by_category(),
        demo_mode=demo,
    )


@ops_bp.route("/not-responding")
def not_responding_page():
    return render_template("ops/not_responding.html", demo_mode=_demo_mode())


@ops_bp.route("/queue-performance")
def queue_performance_page():
    blocked = require_queue_performance_access()
    if blocked:
        return blocked
    from flask import session

    return render_template(
        "ops/queue_performance.html",
        demo_mode=_demo_mode(),
        auth_enabled=queue_performance_auth_enabled(),
        auth_user_name=session.get("genesys_user_name") or "",
    )


_CORPORATE_TRAINING_STATIC = PROJECT_ROOT / "static" / "corporate_training"


@ops_bp.route("/corporate-training")
@ops_bp.route("/corporate-training/")
def corporate_training_page():
    return send_from_directory(_CORPORATE_TRAINING_STATIC, "index.html")


@ops_bp.route("/corporate-training/assets/<path:filename>")
def corporate_training_asset(filename: str):
    return send_from_directory(_CORPORATE_TRAINING_STATIC, filename)


@ops_bp.route("/corporate-training/api/setup")
def corporate_training_setup():
    from app.corporate_training.routes import setup_api

    return setup_api()


@ops_bp.route("/corporate-training/api/start-training", methods=["POST"])
def corporate_training_start():
    from app.corporate_training.routes import start_training_api

    return start_training_api()


@ops_bp.route("/corporate-training/api/return-from-training", methods=["POST"])
def corporate_training_return():
    from app.corporate_training.routes import return_from_training_api

    return return_from_training_api()


@ops_bp.route("/queue-performance/logout")
def queue_performance_logout():
    from app.agent_workspace.oauth import clear_session
    from flask import redirect, session, url_for

    clear_session(session)
    session.pop("queue_performance_allowed", None)
    session.pop("genesys_role_names", None)
    return redirect(url_for("ops.queue_performance_page"))


@ops_bp.route("/api/queue-performance")
def queue_performance_api():
    blocked = require_queue_performance_access()
    if blocked:
        return blocked
    try:
        client = create_client(log=tool_log)
        filters = parse_filters(request.args)
        return jsonify(fetch_queue_performance(client, filters))
    except Exception as exc:
        probe = credentials_env_probe()
        payload = {"error": str(exc), "env_probe": probe}
        if not probe.get("GENESYS_CLIENT_ID") or not probe.get("GENESYS_CLIENT_SECRET"):
            payload["missing_env"] = missing_admin_credentials_message()
        return jsonify(payload), 500


@ops_bp.route("/api/queue-performance/queues/<queue_id>")
def queue_performance_detail_api(queue_id: str):
    blocked = require_queue_performance_access()
    if blocked:
        return blocked
    try:
        client = create_client(log=tool_log)
        filters = parse_filters(request.args)
        return jsonify(fetch_queue_detail(client, queue_id, filters))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        probe = credentials_env_probe()
        payload = {"error": str(exc), "env_probe": probe}
        if not probe.get("GENESYS_CLIENT_ID") or not probe.get("GENESYS_CLIENT_SECRET"):
            payload["missing_env"] = missing_admin_credentials_message()
        return jsonify(payload), 500


@ops_bp.route("/api/queue-performance/export.pdf")
def queue_performance_export_pdf():
    blocked = require_queue_performance_access()
    if blocked:
        return blocked
    try:
        from tools.reporting.queue_performance_pdf_report import (
            generate_detail_pdf,
            generate_overview_pdf,
            pdf_filename,
        )

        client = create_client(log=tool_log)
        filters = parse_filters(request.args)
        queue_id = safe_str(request.args.get("queue_id"))
        if queue_id:
            pdf_bytes, payload = generate_detail_pdf(client, queue_id, filters)
            queue_name = (payload.get("queue") or {}).get("queue_name")
            filename = pdf_filename(filters, queue_name=queue_name)
        else:
            pdf_bytes, _payload = generate_overview_pdf(client, filters)
            filename = pdf_filename(filters)
        return Response(
            pdf_bytes,
            mimetype="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ops_bp.route("/api/queue-performance/export.xlsx")
def queue_performance_export_excel():
    blocked = require_queue_performance_access()
    if blocked:
        return blocked
    try:
        from app.queue_performance_export import build_workbook_bytes, excel_filename

        client = create_client(log=tool_log)
        filters = parse_filters(request.args)
        queue_id = safe_str(request.args.get("queue_id"))
        if queue_id:
            payload = fetch_queue_detail(client, queue_id, filters)
            xlsx_bytes = build_workbook_bytes(payload, detail=True)
            filename = excel_filename(filters, queue_name=(payload.get("queue") or {}).get("queue_name"))
        else:
            payload = fetch_queue_performance(client, filters)
            xlsx_bytes = build_workbook_bytes(payload, detail=False)
            filename = excel_filename(filters)
        return Response(
            xlsx_bytes,
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 404
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ops_bp.route("/api/not-responding")
def not_responding_api():
    try:
        client = create_client(log=tool_log)
        return jsonify(fetch_not_responding_agents(client))
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ops_bp.route("/health")
def health():
    from flask import current_app

    probe = credentials_env_probe()
    corporate_routes = sorted(
        rule.rule
        for rule in current_app.url_map.iter_rules()
        if "corporate-training" in rule.rule
    )
    try:
        client = create_client(log=tool_log)
        org = client.request("GET", "/api/v2/organizations/me")
        return jsonify({
            "ok": True,
            "app": "GenesysToolkit",
            "service": "ops-console",
            "demo_mode": _demo_mode(),
            "organization": org.get("name"),
            "region": client.region,
            "api_host": client.api_host,
            "env_probe": probe,
            "credential_source": credential_source(),
            "admin_credentials_set": probe.get("GENESYS_CLIENT_ID") and probe.get("GENESYS_CLIENT_SECRET"),
            "agent_credentials_set": agent_credentials_present(),
            "corporate_training_routes": corporate_routes,
            "corporate_training_loaded": _CORPORATE_TRAINING_ROUTES_LOADED,
        })
    except Exception as exc:
        return jsonify({
            "ok": False,
            "app": "GenesysToolkit",
            "service": "ops-console",
            "error": str(exc),
            "env_probe": probe,
            "credential_source": credential_source(),
            "admin_credentials_set": bool(probe.get("GENESYS_CLIENT_ID") and probe.get("GENESYS_CLIENT_SECRET")),
            "agent_credentials_set": agent_credentials_present(),
            "credentials_ready": client_credentials_present(),
            "missing_env": missing_admin_credentials_message(),
        }), 500


@ops_bp.route("/api/tool/<tool_id>")
def tool_detail(tool_id: str):
    tool = get_tool(tool_id)
    if not tool:
        return jsonify({"error": "not found"}), 404
    return jsonify(tool)


@ops_bp.route("/api/options/<option_type>")
def api_options(option_type: str):
    try:
        client = create_client(log=tool_log)
        if option_type == "divisions":
            items = client.paged_get("/api/v2/authorization/divisions")
            return jsonify([{"value": d.get("name"), "label": d.get("name")} for d in items if d.get("name")])

        if option_type == "queue_ids":
            items = client.paged_get("/api/v2/routing/queues")
            return jsonify([
                {"value": q.get("id"), "label": q.get("name")}
                for q in items if q.get("id") and q.get("name")
            ])

        if option_type == "queues":
            prefix = request.args.get("prefix", "").strip().lower()
            items = client.paged_get("/api/v2/routing/queues")
            if prefix:
                items = [q for q in items if safe_str(q.get("name")).lower().startswith(prefix)]
            return jsonify([
                {"value": q.get("name"), "label": q.get("name"), "id": q.get("id")}
                for q in items if q.get("name")
            ])

        if option_type == "queue_prefixes":
            prefixes: set[str] = set()
            for queue in client.paged_get("/api/v2/routing/queues"):
                name = safe_str(queue.get("name"))
                if " - " in name:
                    prefixes.add(name.split(" - ", 1)[0].strip())
                elif "_" in name:
                    prefixes.add(name.split("_", 1)[0].strip())
            return jsonify([{"value": p, "label": p} for p in sorted(prefixes)])

        if option_type == "groups":
            items = client.paged_get("/api/v2/groups")
            return jsonify([{"value": g.get("name"), "label": g.get("name")} for g in items if g.get("name")])

        return jsonify([])
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@ops_bp.route("/download-template/<tool_id>")
def download_template(tool_id: str):
    tool = get_tool(tool_id)
    if not tool or not tool.get("template"):
        return jsonify({"error": "no template"}), 404
    return send_from_directory(PROJECT_ROOT / "templates_csv", tool["template"], as_attachment=True)


@ops_bp.route("/download-output/<path:filename>")
def download_output(filename: str):
    ensure_runtime_dirs()
    return send_from_directory(OUTPUTS_DIR, filename, as_attachment=True)


@ops_bp.route("/run/<tool_id>", methods=["POST"])
def run_tool(tool_id: str):
    tool = get_tool(tool_id)
    if not tool:
        return Response("[ERROR] Unknown tool\n", mimetype="text/plain", status=404)

    demo = _demo_mode()
    execute = request.form.get("execute_mode") == "1"

    if demo:
        execute = False
    elif execute and tool.get("mutating"):
        confirm = request.form.get("execute_confirm", "").strip().upper()
        if confirm != "EXECUTE":
            return Response(
                "[ERROR] Execute mode requires typing EXECUTE in the confirmation field.\n",
                mimetype="text/plain",
                status=400,
            )

    form = {key: request.form.get(key, "") for key in request.form.keys()}
    files = {key: request.files[key] for key in request.files.keys() if request.files[key].filename}

    return Response(
        stream_tool_run(tool_id, form, files, execute=execute, demo_mode=demo),
        mimetype="text/plain; charset=utf-8",
    )


def create_app(*, demo_mode: bool = False):
    from datetime import timedelta

    from flask import Flask

    if demo_mode:
        os.environ["OPS_DEMO_MODE"] = "1"
    elif "OPS_DEMO_MODE" not in os.environ:
        os.environ.pop("OPS_DEMO_MODE", None)

    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    app.config["DEMO_MODE"] = demo_mode or is_demo_mode()
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "genesys-toolkit-demo" if demo_mode else "genesys-toolkit-local")
    on_render = os.environ.get("RENDER") == "true"
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = on_render or os.environ.get("FLASK_SECURE_COOKIES") == "1"
    app.register_blueprint(ops_bp)
    from app.ask_genesys import ask_bp

    app.register_blueprint(ask_bp)
    return app


_CORPORATE_TRAINING_ROUTES_LOADED = True
