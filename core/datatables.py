"""Genesys Architect data tables (flows/datatables)."""
from __future__ import annotations

import os
from typing import Any

from core.genesys_client import GenesysClient, GenesysError, ensure_e164, find_by_name, safe_str


def datatable_name_from_env() -> str:
    return (
        os.environ.get("CALL_MANAGERS_DATATABLE_NAME")
        or os.environ.get("CARE_MANAGERS_LOOKUP_DATATABLE_NAME")
        or "Care Managers Lookup"
    ).strip()


def find_datatable_by_name(client: GenesysClient, name: str) -> dict[str, Any]:
    wanted = safe_str(name).strip().lower()
    for row in client.paged_get("/api/v2/flows/datatables"):
        if safe_str(row.get("name")).strip().lower() == wanted:
            return row
    raise GenesysError(f"Data table not found: {name}")


def load_datatable_with_schema(client: GenesysClient, datatable_id: str) -> dict[str, Any]:
    return client.request(
        "GET",
        f"/api/v2/flows/datatables/{datatable_id}",
        params={"expand": "schema"},
    )


def _schema_properties(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    props = schema.get("properties") if isinstance(schema, dict) else None
    return props if isinstance(props, dict) else {}


def schema_property_key(schema: dict[str, Any], *logical_names: str) -> str:
    """Resolve API field id from schema property name or title."""
    props = _schema_properties(schema)
    wanted = {safe_str(n).strip().lower() for n in logical_names if safe_str(n).strip()}
    for key, spec in props.items():
        if safe_str(key).lower() in wanted:
            return safe_str(key)
        title = safe_str((spec or {}).get("title")).lower()
        if title in wanted:
            return safe_str(key)
    raise GenesysError(f"Schema field not found for: {', '.join(logical_names)}")


def list_datatable_rows(client: GenesysClient, datatable_id: str) -> list[dict[str, Any]]:
    return client.paged_get(f"/api/v2/flows/datatables/{datatable_id}/rows")


def find_row_by_dnis(
    rows: list[dict[str, Any]],
    dnis_e164: str,
    *,
    dnis_field: str,
) -> dict[str, Any] | None:
    target = safe_str(dnis_e164).strip()
    target_digits = "".join(c for c in target if c.isdigit())
    for row in rows:
        key = safe_str(row.get("key"))
        val = safe_str(row.get(dnis_field))
        for candidate in (key, val):
            if not candidate:
                continue
            if candidate == target:
                return row
            cand_digits = "".join(c for c in candidate if c.isdigit())
            if cand_digits and cand_digits == target_digits:
                return row
    return None


def build_call_manager_row_body(
    schema: dict[str, Any],
    *,
    dnis_e164: str,
    queue_name: str,
    center_queue_name: str,
    skill: str = "NCM",
    open_value: bool = True,
) -> dict[str, Any]:
    dnis_key = schema_property_key(schema, "dnis", "key")
    queue_key = schema_property_key(schema, "queuename", "queue name", "queuename")
    skill_key = schema_property_key(schema, "skill")
    center_key = schema_property_key(schema, "centerqueue", "center queue")
    open_key = schema_property_key(schema, "open")

    body: dict[str, Any] = {
        "key": dnis_e164,
        dnis_key: dnis_e164,
        queue_key: queue_name,
        skill_key: skill,
        center_key: center_queue_name,
        open_key: open_value,
    }
    return body


def create_datatable_row(
    client: GenesysClient,
    datatable_id: str,
    body: dict[str, Any],
    *,
    apply: bool,
) -> str:
    if not apply:
        return "would_create_datatable_row"
    client.request(
        "POST",
        f"/api/v2/flows/datatables/{datatable_id}/rows",
        json_body=body,
        expected_statuses=[200, 201],
    )
    return "created_datatable_row"
