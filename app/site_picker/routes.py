from __future__ import annotations

import os
from datetime import timedelta
from functools import wraps
from typing import Any, Callable

from flask import (
    Blueprint,
    Flask,
    Response,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
)

from app.agent_workspace.oauth import (
    OAuthConfigError,
    OAuthExchangeError,
    authorize_url,
    clear_session,
    exchange_code_for_tokens,
    fetch_current_user,
    generate_pkce,
    generate_state,
    persist_session,
    site_picker_oauth_settings,
    site_picker_redirect_uri,
    use_pkce,
)
from app.site_picker.telephony import (
    change_phone_site,
    fetch_sites_with_user_token,
    find_user_web_phone,
    phone_summary,
)
from core.cli import create_client
from core.config import get_apps_host, get_login_host
from core.genesys_client import GenesysError
from core.paths import PROJECT_ROOT

site_picker_bp = Blueprint("site_picker", __name__)

STATIC_DIR = PROJECT_ROOT / "static" / "site_picker"


def _is_authenticated() -> bool:
    return bool(session.get("genesys_user_id") and session.get("genesys_access_token"))


def _access_token() -> str:
    return str(session.get("genesys_access_token") or "")


def _require_auth():
    if _is_authenticated():
        return None
    return jsonify({"error": "Sign in required"}), 401


def _allowed_bridge_origins() -> set[str]:
    origins = {
        "http://127.0.0.1:8765",
        "http://localhost:8765",
        get_apps_host().rstrip("/"),
    }
    render_url = (os.environ.get("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    if render_url:
        origins.add(render_url)
    custom = (os.environ.get("GENESYS_SITE_PICKER_ALLOWED_ORIGINS") or "").strip()
    for part in custom.split(","):
        part = part.strip().rstrip("/")
        if part:
            origins.add(part)
    return origins


def _cors_bridge(f: Callable):
    @wraps(f)
    def wrapped(*args, **kwargs):
        if request.method == "OPTIONS":
            response = Response("", status=204)
        else:
            result = f(*args, **kwargs)
            if isinstance(result, tuple):
                response = result[0]
                if len(result) > 1:
                    response.status_code = result[1]
            else:
                response = result
        origin = request.headers.get("Origin", "")
        if origin.rstrip("/") in _allowed_bridge_origins():
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Credentials"] = "true"
            response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    return wrapped


def _admin_client():
    return create_client()


def _load_state() -> dict[str, Any]:
    if not _is_authenticated():
        return {
            "authenticated": False,
            "user": None,
            "phone": None,
            "sites": [],
            "apps_url": get_apps_host(),
        }

    user = {
        "id": session.get("genesys_user_id"),
        "name": session.get("genesys_user_name"),
        "email": session.get("genesys_user_email"),
    }
    sites: list[dict[str, Any]] = []
    phone = None
    error = ""

    try:
        sites_raw = fetch_sites_with_user_token(_access_token())
        sites = [
            {"id": s.get("id"), "name": s.get("name")}
            for s in sites_raw
            if s.get("id")
        ]
    except GenesysError as exc:
        error = f"Could not load sites: {exc}"

    try:
        admin = _admin_client()
        web_phone = find_user_web_phone(
            admin,
            user_id=str(user["id"]),
            user_name=str(user["name"] or ""),
        )
        phone = phone_summary(web_phone)
    except Exception as exc:
        if not error:
            error = f"Could not load phone: {exc}"
        else:
            error = f"{error}; phone: {exc}"

    state = {
        "authenticated": True,
        "user": user,
        "phone": phone,
        "sites": sites,
        "apps_url": get_apps_host(),
    }
    if error:
        state["error"] = error
    return state


@site_picker_bp.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@site_picker_bp.route("/assets/<path:filename>")
def assets(filename: str):
    return send_from_directory(STATIC_DIR, filename)


@site_picker_bp.route("/health")
def health():
    return jsonify({"ok": True, "service": "site-picker", "authenticated": _is_authenticated()})


@site_picker_bp.route("/api/state")
def api_state():
    return jsonify(_load_state())


@site_picker_bp.route("/api/bridge", methods=["POST", "OPTIONS"])
@_cors_bridge
def api_bridge():
    if request.method == "OPTIONS":
        return Response("", status=204)

    payload = request.get_json(silent=True) or {}
    access_token = str(payload.get("access_token") or "").strip()
    if not access_token:
        return jsonify({"ok": False, "error": "access_token required"}), 400

    try:
        user = fetch_current_user(access_token)
    except OAuthExchangeError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 401

    from app.agent_workspace.oauth import OAuthTokens

    persist_session(
        session,
        tokens=OAuthTokens(
            access_token=access_token,
            refresh_token=None,
            expires_in=None,
            token_type="Bearer",
        ),
        user=user,
    )
    return jsonify({"ok": True, "user": {"id": user.user_id, "name": user.display_name}})


@site_picker_bp.route("/oauth/login")
def oauth_login():
    popup = bool(request.args.get("popup"))
    try:
        verifier: str | None = None
        challenge: str | None = None
        redirect_uri = site_picker_redirect_uri()
        if use_pkce():
            verifier, challenge = generate_pkce()
            session["oauth_pkce_verifier"] = verifier
        else:
            session.pop("oauth_pkce_verifier", None)
        oauth_state = generate_state()
        session["oauth_state"] = oauth_state
        return redirect(
            authorize_url(
                state=oauth_state,
                code_challenge=challenge,
                redirect_uri=redirect_uri,
            )
        )
    except OAuthConfigError as exc:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message=str(exc),
                popup=popup,
            ),
            500,
        )


@site_picker_bp.route("/oauth/callback")
def oauth_callback():
    redirect_uri = site_picker_redirect_uri()
    oauth_error = request.args.get("error")
    if oauth_error:
        description = request.args.get("error_description") or oauth_error
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message=description,
                popup=True,
            ),
            400,
        )

    code = request.args.get("code")
    returned_state = request.args.get("state")
    expected_state = session.get("oauth_state")
    if not code:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message="Missing authorization code from Genesys.",
                popup=True,
            ),
            400,
        )
    if not expected_state or returned_state != expected_state:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message="OAuth state mismatch. Please try signing in again.",
                popup=True,
            ),
            400,
        )

    verifier = session.get("oauth_pkce_verifier")
    if use_pkce() and not verifier:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message="OAuth session expired. Please try signing in again.",
                popup=True,
            ),
            400,
        )

    try:
        tokens = exchange_code_for_tokens(
            code=code,
            code_verifier=verifier or "",
            redirect_uri=redirect_uri,
        )
        user = fetch_current_user(tokens.access_token)
        persist_session(session, tokens=tokens, user=user)
        return render_template(
            "agent_workspace/oauth_callback.html",
            user_name=user.display_name,
        )
    except OAuthExchangeError as exc:
        return (
            render_template(
                "agent_workspace/oauth_error.html",
                message=str(exc),
                popup=True,
            ),
            500,
        )


@site_picker_bp.route("/oauth/config")
def oauth_config():
    try:
        settings = site_picker_oauth_settings()
        return jsonify({
            "login_host": get_login_host(),
            "redirect_uri": settings.redirect_uri,
            "client_id": settings.client_id,
            "pkce": use_pkce(),
            "apps_url": get_apps_host(),
        })
    except OAuthConfigError as exc:
        return jsonify({"error": str(exc)}), 500


@site_picker_bp.route("/oauth/logout", methods=["POST"])
def oauth_logout():
    clear_session(session)
    return jsonify({"ok": True})


@site_picker_bp.route("/api/change-site", methods=["POST"])
def api_change_site():
    blocked = _require_auth()
    if blocked:
        return blocked

    payload = request.get_json(silent=True) or {}
    site_id = str(payload.get("site_id") or "").strip()
    if not site_id:
        return jsonify({"error": "site_id required"}), 400

    user_id = str(session.get("genesys_user_id") or "")
    user_name = str(session.get("genesys_user_name") or "")

    try:
        admin = _admin_client()
        web_phone = find_user_web_phone(admin, user_id=user_id, user_name=user_name)
        if not web_phone:
            return jsonify({"error": "No WebRTC phone found for your user account."}), 404

        phone_id = str(web_phone.get("id") or "")
        allowed = {s.get("id") for s in fetch_sites_with_user_token(_access_token())}
        if site_id not in allowed:
            return jsonify({"error": "You do not have permission for that site."}), 403

        change_phone_site(admin, phone_id=phone_id, site_id=site_id)
        updated = phone_summary(find_user_web_phone(admin, user_id=user_id, user_name=user_name))
        return jsonify({"ok": True, "phone": updated})
    except GenesysError as exc:
        return jsonify({"error": str(exc)}), 502
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


def create_app() -> Flask:
    app = Flask(
        __name__,
        template_folder=str(PROJECT_ROOT / "templates"),
        static_folder=str(PROJECT_ROOT / "static"),
    )
    on_render = os.environ.get("RENDER") == "true"
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "genesys-site-picker-local")
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=12)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_SECURE"] = on_render or os.environ.get("FLASK_SECURE_COOKIES") == "1"
    app.register_blueprint(site_picker_bp)
    return app
