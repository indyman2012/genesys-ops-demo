from __future__ import annotations

import base64
import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

import requests

from core.config import (
    agent_credentials_from_env,
    get_api_host,
    get_client_credentials,
    get_login_host,
    load_client_env,
    normalize_region,
)
from core.paths import PROJECT_ROOT

LogFn = Callable[[str, str], None]


class GenesysError(RuntimeError):
    pass


def safe_str(value: Any) -> str:
    return "" if value is None else str(value)


def normalize_number(value: Any) -> str:
    digits = re.sub(r"\D", "", safe_str(value))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def ensure_e164(value: Any) -> str:
    raw = safe_str(value).strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return ""
    if raw.startswith("+"):
        return raw
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return "+" + digits


def find_by_name(items: List[Dict[str, Any]], name: str) -> Optional[Dict[str, Any]]:
    target = safe_str(name).strip().lower()
    for item in items:
        if safe_str(item.get("name")).strip().lower() == target:
            return item
    return None


class GenesysClient:
    def __init__(
        self,
        root: Path | None = None,
        log: LogFn | None = None,
        env_path: Path | None = None,
    ):
        self.root = Path(root or PROJECT_ROOT)
        self.log = log or (lambda _level, _msg: None)
        self.env_path = env_path
        self.token = ""
        self.region = normalize_region()
        self.api_host = ""
        self.login_host = ""

    def authenticate(self) -> None:
        load_client_env(self.env_path)
        self.region = normalize_region()
        self.api_host = get_api_host(self.region)
        self.login_host = get_login_host(self.region)
        client_id, client_secret = get_client_credentials()
        basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        response = requests.post(
            f"{self.login_host}/oauth/token",
            headers={
                "Authorization": f"Basic {basic}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=60,
        )
        if response.status_code >= 400:
            detail = response.text[:500]
            if "client_credential" in detail.lower() and agent_credentials_from_env()[0]:
                raise GenesysError(
                    "OAuth client_credentials grant failed. GENESYS_AGENT_* clients are "
                    "authorization-code only. Set GENESYS_CLIENT_ID/SECRET from a Client "
                    f"Credentials OAuth client. Genesys response: {detail}"
                )
            raise GenesysError(f"OAuth failed: HTTP {response.status_code}: {detail}")
        self.token = response.json()["access_token"]
        self.log("ok", f"Authenticated to {self.region} ({self.api_host})")

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        expected_statuses: Iterable[int] | None = None,
        max_retries: int = 8,
    ) -> Any:
        if not self.token:
            self.authenticate()
        expected = set(expected_statuses or [200, 201, 202, 204])
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/json"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        last_error: str | None = None
        for attempt in range(1, max_retries + 1):
            try:
                response = requests.request(
                    method,
                    f"{self.api_host}{path}",
                    headers=headers,
                    params=params,
                    json=json_body,
                    timeout=60,
                )
            except requests.RequestException as exc:
                last_error = str(exc)
                sleep_for = min(2 ** attempt, 30)
                self.log("warn", f"Network error {method} {path}: {exc}; sleeping {sleep_for}s")
                time.sleep(sleep_for)
                continue
            if response.status_code == 429:
                try:
                    sleep_for = int(response.headers.get("Retry-After") or 0) or min(2 ** attempt, 60)
                except ValueError:
                    sleep_for = min(2 ** attempt, 60)
                self.log("warn", f"429 on {method} {path}; sleeping {sleep_for}s")
                time.sleep(sleep_for)
                continue
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}: {response.text}"
                sleep_for = min(2 ** attempt, 30)
                self.log("warn", f"Server error {method} {path}; sleeping {sleep_for}s")
                time.sleep(sleep_for)
                continue
            if response.status_code not in expected:
                raise GenesysError(f"HTTP {response.status_code} on {method} {path}: {response.text}")
            if response.status_code == 204 or not response.text.strip():
                return None
            if "application/json" in response.headers.get("Content-Type", "").lower():
                return response.json()
            return response.text
        raise GenesysError(f"Request failed after retries on {method} {path}: {last_error}")

    def paged_get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        page_size: int = 100,
    ) -> list[dict[str, Any]]:
        page = 1
        out: list[dict[str, Any]] = []
        while True:
            page_params = dict(params or {})
            page_params.update({"pageSize": page_size, "pageNumber": page})
            payload = self.request("GET", path, params=page_params)
            entities: list[Any] = []
            if isinstance(payload, dict):
                entities = (
                    payload.get("entities")
                    or payload.get("items")
                    or payload.get("results")
                    or payload.get("trunkBases")
                    or payload.get("didPools")
                    or payload.get("ivrs")
                    or []
                )
            if not isinstance(entities, list):
                raise GenesysError(f"Unexpected list shape for {path}: {payload}")
            out.extend(entities)
            page_count = payload.get("pageCount") if isinstance(payload, dict) else None
            total = payload.get("total") if isinstance(payload, dict) else None
            if page_count is not None and page >= int(page_count):
                break
            if total is not None and len(out) >= int(total):
                break
            if page_count is None and total is None and len(entities) < page_size:
                break
            page += 1
        return out
