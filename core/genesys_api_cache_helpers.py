from __future__ import annotations

import base64
import os
from typing import Any

import requests

from core.config import CREDENTIAL_ENV_HINT, client_credentials_from_env, env_first, load_client_env

REGION_HOSTS = {
    "usw2": "https://api.usw2.pure.cloud",
    "us-west-2": "https://api.usw2.pure.cloud",
    "us_east_1": "https://api.mypurecloud.com",
    "us-east-1": "https://api.mypurecloud.com",
    "us": "https://api.mypurecloud.com",
    "mypurecloud.com": "https://api.mypurecloud.com",
}


def get_api_base() -> str:
    load_client_env()
    explicit = env_first("GENESYS_API_BASE", "PURECLOUD_API_BASE")
    if explicit:
        return explicit.rstrip("/")

    region = (env_first("GENESYS_REGION", "PURECLOUD_REGION", default="usw2") or "usw2").lower()
    return REGION_HOSTS.get(region, f"https://api.{region}.pure.cloud").rstrip("/")


def get_access_token() -> str:
    load_client_env()
    client_id, client_secret = client_credentials_from_env()
    if not client_id or not client_secret:
        raise RuntimeError(
            "Missing GENESYS_CLIENT_ID / GENESYS_CLIENT_SECRET. " + CREDENTIAL_ENV_HINT
        )

    api_base = get_api_base()
    token_url = f"{api_base}/oauth/token"

    raw = f"{client_id}:{client_secret}".encode("utf-8")
    auth_header = base64.b64encode(raw).decode("ascii")

    response = requests.post(
        token_url,
        headers={
            "Authorization": f"Basic {auth_header}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["access_token"]


def genesys_get(path: str, token: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    api_base = get_api_base()
    url = f"{api_base}{path}"

    response = requests.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params=params or {},
        timeout=60,
    )
    response.raise_for_status()
    return response.json()
