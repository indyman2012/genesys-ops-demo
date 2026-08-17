"""Genesys Cloud OAuth (authorization code + PKCE) for agent workspace pilot."""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import requests

from core.config import env_first, get_api_host, get_login_host


class OAuthConfigError(RuntimeError):
    pass


class OAuthExchangeError(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentOAuthSettings:
    client_id: str
    client_secret: str
    redirect_uri: str


@dataclass(frozen=True)
class OAuthTokens:
    access_token: str
    refresh_token: str | None
    expires_in: int | None
    token_type: str | None


@dataclass(frozen=True)
class AgentUser:
    user_id: str
    display_name: str
    email: str | None


def agent_oauth_settings(*, redirect_uri: str | None = None) -> AgentOAuthSettings:
    client_id = (env_first("GENESYS_AGENT_CLIENT_ID") or "").strip()
    client_secret = (env_first("GENESYS_AGENT_CLIENT_SECRET") or "").strip()
    resolved_redirect = (redirect_uri or env_first("GENESYS_AGENT_REDIRECT_URI") or "").strip()
    missing = [
        name
        for name, value in (
            ("GENESYS_AGENT_CLIENT_ID", client_id),
            ("GENESYS_AGENT_CLIENT_SECRET", client_secret),
            ("GENESYS_AGENT_REDIRECT_URI", resolved_redirect),
        )
        if not value
    ]
    if missing:
        raise OAuthConfigError(f"Missing agent OAuth env vars: {', '.join(missing)}")
    return AgentOAuthSettings(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=resolved_redirect,
    )


def site_picker_mount_path() -> str:
    explicit = (env_first("GENESYS_SITE_PICKER_MOUNT_PATH") or "").strip().rstrip("/")
    if explicit:
        return explicit if explicit.startswith("/") else f"/{explicit}"
    if (env_first("RENDER_EXTERNAL_URL") or "").strip():
        return "/site-picker"
    return ""


def site_picker_redirect_uri() -> str:
    explicit = (env_first("GENESYS_SITE_PICKER_REDIRECT_URI") or "").strip()
    if explicit:
        return explicit
    render_url = (env_first("RENDER_EXTERNAL_URL") or "").strip().rstrip("/")
    mount = site_picker_mount_path()
    if render_url:
        return f"{render_url}{mount}/oauth/callback"
    port = (env_first("SITE_PICKER_PORT") or "8765").strip()
    return f"http://127.0.0.1:{port}{mount}/oauth/callback"


def site_picker_oauth_settings() -> AgentOAuthSettings:
    return agent_oauth_settings(redirect_uri=site_picker_redirect_uri())


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode("ascii")).digest()
    ).decode("ascii").rstrip("=")
    return verifier, challenge


def generate_state() -> str:
    return secrets.token_urlsafe(32)


def use_pkce() -> bool:
    value = (env_first("GENESYS_AGENT_USE_PKCE", default="1") or "1").strip().lower()
    return value not in ("0", "false", "no", "off")


def authorize_url(
    *,
    state: str,
    code_challenge: str | None = None,
    redirect_uri: str | None = None,
) -> str:
    settings = agent_oauth_settings(redirect_uri=redirect_uri)
    params = {
        "response_type": "code",
        "client_id": settings.client_id,
        "redirect_uri": settings.redirect_uri,
        "state": state,
    }
    if use_pkce():
        if not code_challenge:
            raise OAuthConfigError("PKCE enabled but code_challenge missing")
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    return f"{get_login_host()}/oauth/authorize?{urlencode(params)}"


def exchange_code_for_tokens(
    *,
    code: str,
    code_verifier: str,
    redirect_uri: str | None = None,
) -> OAuthTokens:
    settings = agent_oauth_settings(redirect_uri=redirect_uri)
    login_host = get_login_host()
    basic = base64.b64encode(
        f"{settings.client_id}:{settings.client_secret}".encode("ascii")
    ).decode("ascii")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.redirect_uri,
    }
    if use_pkce():
        data["code_verifier"] = code_verifier
    response = requests.post(
        f"{login_host}/oauth/token",
        headers={
            "Authorization": f"Basic {basic}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data=data,
        timeout=60,
    )
    if response.status_code >= 400:
        raise OAuthExchangeError(
            f"Token exchange failed ({response.status_code}): {response.text[:500]}"
        )
    payload = response.json()
    access_token = payload.get("access_token")
    if not access_token:
        raise OAuthExchangeError("Token response missing access_token")
    return OAuthTokens(
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_in=payload.get("expires_in"),
        token_type=payload.get("token_type"),
    )


def fetch_current_user(access_token: str) -> AgentUser:
    api_host = get_api_host()
    response = requests.get(
        f"{api_host}/api/v2/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=60,
    )
    if response.status_code >= 400:
        raise OAuthExchangeError(
            f"users/me failed ({response.status_code}): {response.text[:500]}"
        )
    payload: dict[str, Any] = response.json()
    user_id = str(payload.get("id") or "").strip()
    if not user_id:
        raise OAuthExchangeError("users/me response missing id")
    name = str(payload.get("name") or payload.get("username") or user_id).strip()
    email = payload.get("email")
    return AgentUser(
        user_id=user_id,
        display_name=name,
        email=str(email).strip() if email else None,
    )


def persist_session(session: dict[str, Any], *, tokens: OAuthTokens, user: AgentUser) -> None:
    session["genesys_user_id"] = user.user_id
    session["genesys_user_name"] = user.display_name
    if user.email:
        session["genesys_user_email"] = user.email
    session["genesys_access_token"] = tokens.access_token
    if tokens.refresh_token:
        session["genesys_refresh_token"] = tokens.refresh_token
    session.pop("oauth_pkce_verifier", None)
    session.pop("oauth_state", None)
    session.permanent = True


def clear_session(session: dict[str, Any]) -> None:
    for key in (
        "genesys_user_id",
        "genesys_user_name",
        "genesys_user_email",
        "genesys_access_token",
        "genesys_refresh_token",
        "oauth_pkce_verifier",
        "oauth_state",
    ):
        session.pop(key, None)
