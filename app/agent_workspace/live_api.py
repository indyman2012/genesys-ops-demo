"""Shared Genesys API client for agent workspace live sessions."""
from __future__ import annotations

from typing import Any

import requests

from core.config import get_api_host


class LiveQueueError(RuntimeError):
    pass


class UserApiClient:
    def __init__(self, access_token: str) -> None:
        self.api_host = get_api_host().rstrip("/")
        self.access_token = access_token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
    ) -> Any:
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        response = requests.request(
            method,
            f"{self.api_host}{path}",
            headers=headers,
            params=params,
            json=json_body,
            timeout=60,
        )
        if response.status_code == 401:
            raise LiveQueueError("Genesys session expired — sign in again.")
        if response.status_code >= 400:
            raise LiveQueueError(
                f"Genesys API {method} {path} failed ({response.status_code}): "
                f"{response.text[:300]}"
            )
        if response.status_code == 204 or not response.text.strip():
            return None
        return response.json()

    def paged_get(self, path: str, *, page_size: int = 100) -> list[dict[str, Any]]:
        page = 1
        rows: list[dict[str, Any]] = []
        while True:
            payload = self.request(
                "GET",
                path,
                params={"pageSize": page_size, "pageNumber": page},
            )
            entities = (payload or {}).get("entities") or []
            if not isinstance(entities, list):
                raise LiveQueueError(f"Unexpected list shape from {path}")
            rows.extend(entities)
            page_count = int((payload or {}).get("pageCount") or 1)
            if page >= page_count:
                break
            page += 1
        return rows
