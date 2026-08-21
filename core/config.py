from __future__ import annotations

import os
from pathlib import Path

from core.paths import PROJECT_ROOT

REGION_ALIASES: dict[str, str] = {
    "usw2": "usw2",
    "us-west-2": "usw2",
    "us_west_2": "usw2",
    "us-east-1": "mypurecloud.com",
    "us_east_1": "mypurecloud.com",
    "us": "mypurecloud.com",
    "mypurecloud.com": "mypurecloud.com",
    "usw2.pure.cloud": "usw2",
}

DEFAULT_REGION = "usw2"

CREDENTIAL_ENV_HINT = (
    "Set GENESYS_CLIENT_ID and GENESYS_CLIENT_SECRET in the host environment "
    "(Render dashboard → Environment), or create client.env for local development."
)


def env_first(*names: str, default: str | None = None) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value not in (None, ""):
            return value
    return default


def client_credentials_from_env() -> tuple[str | None, str | None]:
    client_id = env_first("GENESYS_CLIENT_ID", "PURECLOUD_CLIENT_ID", "CLIENT_ID")
    client_secret = env_first("GENESYS_CLIENT_SECRET", "PURECLOUD_CLIENT_SECRET", "CLIENT_SECRET")
    return client_id, client_secret


def client_credentials_present() -> bool:
    client_id, client_secret = client_credentials_from_env()
    return bool(client_id and client_secret)


def load_client_env(env_path: Path | None = None) -> Path | None:
    """Load Genesys credentials from process env or client.env (local dev).

    Process environment variables always win. When GENESYS_CLIENT_ID and
    GENESYS_CLIENT_SECRET are already set (e.g. on Render), no file is required.
    """
    if client_credentials_present():
        return None

    candidates = [
        env_path,
        Path.cwd() / "client.env",
        PROJECT_ROOT / "client.env",
    ]
    for candidate in candidates:
        if candidate is None or not candidate.exists():
            continue
        for raw in candidate.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        if client_credentials_present():
            return candidate

    raise FileNotFoundError(
        "Genesys client credentials not found. "
        f"{CREDENTIAL_ENV_HINT} Expected file path: {PROJECT_ROOT / 'client.env'}"
    )


def normalize_region(raw: str | None = None) -> str:
    if not client_credentials_present():
        try:
            load_client_env()
        except FileNotFoundError:
            pass
    value = (raw or env_first(
        "GENESYS_REGION",
        "PURECLOUD_REGION",
        "GENESYS_CLOUD_REGION",
        default=DEFAULT_REGION,
    ) or DEFAULT_REGION).strip().lower()
    value = value.removeprefix("https://api.").removeprefix("https://login.")
    if value.startswith("api."):
        value = value[4:]
    return REGION_ALIASES.get(value, value.split(".")[0] if "." in value else value)


def region_domain(region: str | None = None) -> str:
    """Host suffix for api.{domain} and login.{domain}."""
    key = normalize_region(region)
    if key == "mypurecloud.com":
        return "mypurecloud.com"
    if "." in key:
        return key
    return f"{key}.pure.cloud"


def get_login_host(region: str | None = None) -> str:
    explicit = env_first("GENESYS_LOGIN_BASE", "PURECLOUD_LOGIN_BASE")
    if explicit:
        return explicit.rstrip("/")
    domain = region_domain(region)
    if domain == "mypurecloud.com":
        return "https://login.mypurecloud.com"
    return f"https://login.{domain}"


def get_api_host(region: str | None = None) -> str:
    explicit = env_first("GENESYS_API_BASE", "PURECLOUD_API_BASE")
    if explicit:
        return explicit.rstrip("/")
    return f"https://api.{region_domain(region)}"


def get_apps_host(region: str | None = None) -> str:
    explicit = env_first("GENESYS_APPS_BASE", "PURECLOUD_APPS_BASE")
    if explicit:
        return explicit.rstrip("/")
    domain = region_domain(region)
    if domain == "mypurecloud.com":
        return "https://apps.mypurecloud.com"
    return f"https://apps.{domain}"


def genesys_analytics_dashboards_url(region: str | None = None) -> str:
    return f"{get_apps_host(region)}/directory/#/analytics/dashboards"


def get_client_credentials() -> tuple[str, str]:
    load_client_env()
    client_id, client_secret = client_credentials_from_env()
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing GENESYS_CLIENT_ID / GENESYS_CLIENT_SECRET. " + CREDENTIAL_ENV_HINT
        )
    return client_id, client_secret
