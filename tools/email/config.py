"""Email/SMTP configuration for toolkit reports."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from core.paths import PROJECT_ROOT

DEFAULT_WORK_TO = "doug.hendrickson@archwellhealth.com"
DEFAULT_FROM_NAME = "Doug Hendrickson - Cursor Report Engine"

GMAIL_SMTP_HOST = "smtp.gmail.com"
GMAIL_SMTP_PORT = 587

O365_SMTP_HOST = "smtp.office365.com"
O365_SMTP_PORT = 587

DEFAULT_ENV_DOUG = PROJECT_ROOT / "doug.env"
DEFAULT_ENV_EMAIL = PROJECT_ROOT / "email.env"


def load_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        raise FileNotFoundError(path)
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def default_email_env_file() -> Path:
    """Prefer email.env when present; fall back to doug.env for local Gmail dev."""
    if DEFAULT_ENV_EMAIL.exists():
        return DEFAULT_ENV_EMAIL
    return DEFAULT_ENV_DOUG


def _truthy(value: str | None, *, default: bool = True) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class SmtpConfig:
    host: str
    port: int
    use_tls: bool
    user: str
    password: str
    from_address: str
    from_name: str
    provider: str


_ENV_KEYS = (
    "EMAIL_PROVIDER",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_USE_TLS",
    "SMTP_USER",
    "SMTP_PASSWORD",
    "SMTP_FROM",
    "FROM_NAME",
    "SENDER_NAME",
    "USER",
    "PASSWORD",
    "GMAIL_USER",
    "GMAIL_APP_PASSWORD",
    "APP_PASSWORD",
    "EMAIL",
)


def _is_office365(env: dict[str, str]) -> bool:
    provider = (env.get("EMAIL_PROVIDER") or "").strip().lower()
    host = (env.get("SMTP_HOST") or "").strip().lower()
    return provider in ("office365", "o365", "outlook") or host == O365_SMTP_HOST


def merged_email_env(env_path: Path | None) -> dict[str, str]:
    """Merge env file values with process environment (process env wins)."""
    merged: dict[str, str] = {}
    if env_path and env_path.exists():
        merged.update(load_env_file(env_path))
    for key in _ENV_KEYS:
        if key in os.environ and os.environ[key].strip():
            merged[key] = os.environ[key].strip()
    return merged


def smtp_config(env_path: Path | None = None, *, require_credentials: bool = True) -> SmtpConfig:
    path = env_path or default_email_env_file()
    env = merged_email_env(path if (env_path or path.exists()) else None)

    office365 = _is_office365(env)

    host = (env.get("SMTP_HOST") or "").strip()
    if not host:
        host = O365_SMTP_HOST if office365 else GMAIL_SMTP_HOST

    port_str = (env.get("SMTP_PORT") or "").strip()
    if port_str:
        port = int(port_str)
    elif office365 or host.lower() == O365_SMTP_HOST:
        port = O365_SMTP_PORT
    else:
        port = GMAIL_SMTP_PORT

    use_tls = _truthy(env.get("SMTP_USE_TLS"), default=True)

    user = (
        env.get("SMTP_USER")
        or env.get("USER")
        or env.get("GMAIL_USER")
        or env.get("EMAIL")
        or ""
    ).strip()
    password = (
        env.get("SMTP_PASSWORD")
        or env.get("PASSWORD")
        or env.get("GMAIL_APP_PASSWORD")
        or env.get("APP_PASSWORD")
        or ""
    ).strip()

    from_address = (env.get("SMTP_FROM") or env.get("FROM") or user).strip()
    from_name = (env.get("FROM_NAME") or env.get("SENDER_NAME") or DEFAULT_FROM_NAME).strip()

    if office365 or host.lower() == O365_SMTP_HOST:
        provider = "office365"
    elif host.lower() == GMAIL_SMTP_HOST:
        provider = "gmail"
    else:
        provider = "smtp"

    if require_credentials and (not user or not password):
        raise SystemExit(
            f"Email credentials missing in {path} (and process env).\n"
            "Define SMTP_USER + SMTP_PASSWORD, or USER + PASSWORD for Gmail.\n"
            "See tools/email/EMAIL_SETUP.md"
        )
    if not user:
        user = "smtp-user@example.com"
    if not password:
        password = ""

    return SmtpConfig(
        host=host,
        port=port,
        use_tls=use_tls,
        user=user,
        password=password,
        from_address=from_address,
        from_name=from_name,
        provider=provider,
    )
