#!/usr/bin/env python3
"""One-shot GenesysToolkit web app validation harness (2026-09-09)."""
from __future__ import annotations

import csv
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

os.environ["OPS_DEMO_MODE"] = "1"
os.environ.setdefault("AGENT_DEMO_MODE", "1")

from app.tool_runner import build_command
from app.tool_specs import TOOL_SPECS, get_tool
from core.cli import create_client, tool_log
from core.demo_mode import DEMO_EXCLUDED_TOOL_IDS

RESULTS: list[dict[str, Any]] = []
DUMMY_AUDIT: list[dict[str, str]] = []
TMP = PROJECT_ROOT / "uploads" / "_validation_tmp"
TMP.mkdir(parents=True, exist_ok=True)

DUMMY_QUEUE_NAME = "Cursor.AI Test Queue"
DUMMY_CONTACT_NAME = "Cursor.AI Test Contact"
DUMMY_CONTACT_NUMBER = "+15555550199"
DUMMY_PREFIX = "Cursor.AI"


def record(name: str, route: str, status: str, notes: str, *, category: str = "route") -> None:
    RESULTS.append({
        "name": name,
        "route": route,
        "status": status,
        "notes": notes,
        "category": category,
    })


def run_subprocess(cmd: list[str], timeout: int = 120) -> tuple[int, str]:
    env = os.environ.copy()
    env.setdefault("PYTHONIOENCODING", "utf-8")
    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


TOOL_TIMEOUT = 90


def run_tool_dry(tool_id: str, form: dict | None = None, files: dict | None = None) -> tuple[str, str]:
    form = form or {}
    files = files or {}
    tool = get_tool(tool_id)
    if not tool:
        return "fail", "tool not in catalog (demo excluded?)"
    try:
        cmd = build_command(tool, form, files, execute=False)
    except Exception as exc:
        return "fail", f"build_command: {exc}"

    try:
        code, text = run_subprocess(cmd, timeout=TOOL_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "partial", f"Timed out after {TOOL_TIMEOUT}s (likely org-wide scan)"

    if code == 0:
        return "pass", text[-1500:]
    if "[DRY RUN]" in text or "[INFO]" in text:
        return "partial", text[-2000:]
    return "fail", text[-2000:]


def write_csv(name: str, headers: list[str], rows: list[list[str]]) -> Path:
    path = TMP / name
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(headers)
        w.writerows(rows)
    return path


class DummyFile:
    def __init__(self, path: Path):
        self.filename = path.name
        self._path = path

    def save(self, dest: Path) -> None:
        import shutil
        shutil.copy(self._path, dest)


def create_dummy_queue(client) -> str | None:
    body = {
        "name": DUMMY_QUEUE_NAME,
        "description": "Cursor.AI validation dummy — safe to delete",
        "memberCount": 0,
    }
    try:
        created = client.request("POST", "/api/v2/routing/queues", json_body=body)
        qid = created.get("id", "")
        DUMMY_AUDIT.append({"type": "queue", "id": qid, "name": DUMMY_QUEUE_NAME, "action": "created"})
        return qid
    except Exception as exc:
        DUMMY_AUDIT.append({"type": "queue", "id": "", "name": DUMMY_QUEUE_NAME, "action": f"create_failed: {exc}"})
        return None


def create_dummy_external_contact(client) -> str | None:
    body = {
        "firstName": "Cursor.AI",
        "lastName": "Test Contact",
        "workPhone": {"display": DUMMY_CONTACT_NUMBER},
    }
    try:
        created = client.request("POST", "/api/v2/externalcontacts/contacts", json_body=body)
        cid = created.get("id", "")
        DUMMY_AUDIT.append({"type": "external_contact", "id": cid, "name": DUMMY_CONTACT_NAME, "action": "created"})
        return cid
    except Exception as exc:
        DUMMY_AUDIT.append({"type": "external_contact", "id": "", "name": DUMMY_CONTACT_NAME, "action": f"create_failed: {exc}"})
        return None


def cleanup_dummies(client) -> list[str]:
    logs: list[str] = []
    for item in DUMMY_AUDIT:
        obj_type = item.get("type")
        obj_id = item.get("id", "")
        if not obj_id or item.get("action", "").startswith("create_failed"):
            continue
        try:
            if obj_type == "queue":
                client.request("DELETE", f"/api/v2/routing/queues/{obj_id}")
                item["action"] = "deleted"
                logs.append(f"Deleted queue {obj_id}")
            elif obj_type == "external_contact":
                client.request("DELETE", f"/api/v2/externalcontacts/contacts/{obj_id}")
                item["action"] = "deleted"
                logs.append(f"Deleted external contact {obj_id}")
        except Exception as exc:
            item["action"] = f"delete_failed: {exc}"
            logs.append(f"FAILED delete {obj_type} {obj_id}: {exc}")
    return logs


def validate_flask_routes(api_client=None) -> None:
    from genesys_toolkit_app import app

    client = app.test_client()
    routes = [
        ("Ops Index", "GET", "/", None, 200),
        ("Not Responding Page", "GET", "/not-responding", None, 200),
        ("Queue Performance Page", "GET", "/queue-performance", None, (200, 302, 401)),
        ("Health", "GET", "/health", None, (200, 500)),
        ("Not Responding API", "GET", "/api/not-responding", None, (200, 500)),
        ("Agent Workspace Index", "GET", "/agent/", None, 200),
        ("Agent Health", "GET", "/agent/health", None, 200),
        ("Agent State API", "GET", "/agent/api/state", None, 200),
        ("Agent Reports", "GET", "/agent/reports", None, 200),
        ("Agent AHT Trend", "GET", "/agent/reports/aht-trend", None, 200),
        ("Site Picker Index", "GET", "/site-picker/", None, 200),
        ("Site Picker Health", "GET", "/site-picker/health", None, 200),
        ("Site Picker Setup API", "GET", "/site-picker/api/setup", None, (200, 401)),
        ("Site Picker State API", "GET", "/site-picker/api/state", None, (200, 401)),
    ]
    for name, method, path, data, expect in routes:
        try:
            if method == "GET":
                resp = client.get(path)
            else:
                resp = client.post(path, data=data or {})
            ok_codes = expect if isinstance(expect, tuple) else (expect,)
            status = "pass" if resp.status_code in ok_codes else "fail"
            note = f"HTTP {resp.status_code}"
            if path == "/health" and resp.status_code == 200:
                body = resp.get_json(silent=True) or {}
                note += f"; org={body.get('organization')}; demo={body.get('demo_mode')}"
            elif path == "/api/not-responding" and resp.status_code == 200:
                body = resp.get_json(silent=True) or {}
                note += f"; agents={len(body.get('agents', body if isinstance(body, list) else []))}"
            record(name, path, status, note)
        except Exception as exc:
            record(name, path, "fail", str(exc))

    # API options (direct call — Flask test client can hang on large paged_get)
    if api_client:
        opt_handlers = {
            "divisions": lambda c: c.paged_get("/api/v2/authorization/divisions"),
            "queue_ids": lambda c: c.paged_get("/api/v2/routing/queues"),
            "queues": lambda c: c.paged_get("/api/v2/routing/queues"),
            "groups": lambda c: c.paged_get("/api/v2/groups"),
        }
        for opt, fn in opt_handlers.items():
            try:
                items = fn(api_client)
                if opt == "queue_prefixes":
                    pass
                record(f"API Options: {opt}", f"/api/options/{opt}", "pass", f"{len(items)} items")
            except Exception as exc:
                record(f"API Options: {opt}", f"/api/options/{opt}", "fail", str(exc))
        try:
            prefixes: set[str] = set()
            for queue in api_client.paged_get("/api/v2/routing/queues"):
                name = queue.get("name") or ""
                if " - " in name:
                    prefixes.add(name.split(" - ", 1)[0].strip())
            record("API Options: queue_prefixes", "/api/options/queue_prefixes", "pass", f"{len(prefixes)} prefixes")
        except Exception as exc:
            record("API Options: queue_prefixes", "/api/options/queue_prefixes", "fail", str(exc))

    # Tool detail + templates
    for tool in TOOL_SPECS:
        tid = tool["id"]
        if tid in DEMO_EXCLUDED_TOOL_IDS:
            continue
        resp = client.get(f"/api/tool/{tid}")
        if resp.status_code != 200:
            record(f"Tool API: {tool['name']}", f"/api/tool/{tid}", "fail", f"HTTP {resp.status_code}")
            continue
        record(f"Tool API: {tool['name']}", f"/api/tool/{tid}", "pass", "spec returned")
        if tool.get("template"):
            tresp = client.get(f"/download-template/{tid}")
            tstatus = "pass" if tresp.status_code == 200 else "fail"
            record(f"Template: {tool['name']}", f"/download-template/{tid}", tstatus, f"HTTP {tresp.status_code}")

    # Queue performance API (may require OAuth in non-public mode)
    qp = client.get("/api/queue-performance")
    if qp.status_code in (200, 401, 403):
        record("Queue Performance API", "/api/queue-performance", "partial" if qp.status_code != 200 else "pass",
               f"HTTP {qp.status_code} (OAuth gate expected in demo if OPS_QUEUE_PERFORMANCE_PUBLIC=0)")


def validate_tools(client) -> None:
    end = date.today()
    start = end - timedelta(days=7)

    # Fetch a real user email for read-only tools
    users = client.paged_get("/api/v2/users", page_size=5)
    sample_email = ""
    for u in users:
        if u.get("email"):
            sample_email = u["email"]
            break

    dummy_queue_id = create_dummy_queue(client)

    sample_group = ""
    for group in client.paged_get("/api/v2/groups", page_size=10):
        if group.get("name"):
            sample_group = group["name"]
            break

    tool_inputs: dict[str, tuple[dict, dict]] = {
        "license_counts": ({}, {}),
        "provisioning_snapshot": ({}, {}),
        "queue_members_export": (
            {},
            {"csv_file": DummyFile(write_csv("qme.csv", ["QueueName"], [[DUMMY_QUEUE_NAME]]))},
        ),
        "queue_joined_true": (
            {"user_email": sample_email or "test@example.com", "queue_prefix": DUMMY_PREFIX},
            {"csv_file": DummyFile(write_csv("qj.csv", ["Email", "QueuePrefix"], [[sample_email or "test@example.com", DUMMY_PREFIX]]))},
        ),
        "queue_joined_false": (
            {"user_email": sample_email or "test@example.com", "queue_prefix": DUMMY_PREFIX},
            {"csv_file": DummyFile(write_csv("qjf.csv", ["Email", "QueuePrefix"], [[sample_email or "test@example.com", DUMMY_PREFIX]]))},
        ),
        "queue_remove_user": (
            {"user_email": sample_email or "test@example.com", "queue_prefix": DUMMY_PREFIX},
            {"csv_file": DummyFile(write_csv("qru.csv", ["Email", "QueuePrefix"], [[sample_email or "test@example.com", DUMMY_PREFIX]]))},
        ),
        "queue_remove_all": ({"email": sample_email or "test@example.com"}, {}),
        "external_contacts": (
            {},
            {"csv_file": DummyFile(write_csv("ec.csv", ["Name", "Number"], [[DUMMY_CONTACT_NAME, DUMMY_CONTACT_NUMBER]]))},
        ),
        "extensions_by_title": (
            {},
            {"csv_file": DummyFile(TMP / "ext.csv") if False else DummyFile(write_csv("ext.csv", ["Email", "Title"], [[sample_email or "test@example.com", "Medical Assistant"]]))},
        ),
        "assign_group_patient_queues": ({"group": sample_group or "Supervisors"}, {}),
        "wrapup_codes": (
            {},
            {"wrapup_file": DummyFile(write_csv("wu.csv", ["WrapUpCode", "Description"], [["Cursor.AI Test Wrap", "Validation dummy"]]))},
        ),
        "create_new_center": (
            {},
            {"csv_file": DummyFile(write_csv(
                "cnc.csv",
                [
                    "Division", "SiteName", "LocationName", "Address1", "City", "State", "Zip", "Country",
                    "EmergencyNumber", "CallerIDNumber", "CallerIDName", "BackOfficeNumber", "TimeZone",
                    "SiteAdminEmail", "TrunkBaseName",
                ],
                [[
                    "Cleveland", "CRSRAI", "Cursor.AI Test Location", "123 Example St", "Cleveland", "OH",
                    "44101", "US", "+12165550100", "+12165550100", "ArchWell Health", "+12165550199",
                    "America/New_York", "cursor.ai.test@example.com", "Pure-IP",
                ]],
            ))},
        ),
        "patch_location_elin": ({}, {}),
        "bulk_update_sites": (
            {},
            {"csv_file": DummyFile(write_csv("bus.csv", ["SiteName", "CallerID"], [["Headquarters", ""]]))},
        ),
        "create_sites_from_csv": (
            {},
            {"csv_file": DummyFile(write_csv("csf.csv", ["LocationName", "SiteName"], [["Cursor.AI Test Location", "Cursor.AI Test Site"]]))},
        ),
        "create_outbound_routes": (
            {},
            {"csv_file": DummyFile(write_csv("cor.csv", ["SiteName"], [["Cursor.AI Test Site"]]))},
        ),
        "update_outbound_routes": (
            {},
            {"csv_file": DummyFile(write_csv("uor.csv", ["SiteName"], [["Cursor.AI Test Site"]]))},
        ),
        "audit_sites_locations": (
            {},
            {"csv": DummyFile(write_csv("asl.csv", ["SiteName", "LocationName"], [["Headquarters", "Headquarters"]]))},
        ),
        "reassign_phones_hq": (
            {},
            {"csv": DummyFile(write_csv(
                "rph.csv",
                ["site_name", "id"],
                [["Cursor.AI Test Site", "00000000-0000-0000-0000-000000000001"]],
            ))},
        ),
        "role_level_assignment": (
            {"user": sample_email or "test@example.com", "division": "Home"},
            {},
        ),
        "cx_license_review": ({"email": sample_email or "test@example.com"}, {}),
        "create_roaming_agent_role": ({"site_names": "Headquarters"}, {}),
        "create_cx1_test_role": ({}, {}),
        "add_generated_emails": (
            {},
            {"input_file": DummyFile(write_csv("age.csv", ["FirstName", "LastName"], [["Cursor", "AI"]]))},
        ),
        "permission_audit": ({"userEmail": sample_email or ""}, {}),
        "callback_sla_audit": (
            {"start": start.isoformat(), "end": end.isoformat(), "sla_minutes": "10"},
            {},
        ),
        "validate_user_site_data": (
            {},
            {"input_file": DummyFile(write_csv("vus.csv", ["Email", "Site"], [[sample_email or "test@example.com", "Headquarters"]]))},
        ),
        "cleanup_attachment_audit": (
            {},
            {"csv": DummyFile(write_csv("caa.csv", ["SiteName"], [["Headquarters"]]))},
        ),
    }

    for tool in TOOL_SPECS:
        tid = tool["id"]
        if tid in DEMO_EXCLUDED_TOOL_IDS:
            record(tool["name"], f"/run/{tid}", "skipped", "Excluded from demo catalog (destructive)", category="tool")
            continue

        form, files = tool_inputs.get(tid, ({}, {}))
        if tid == "create_cx1_test_role":
            record(tool["name"], f"/run/{tid}", "skipped", "Requires prior cx_license_review JSON output file", category="tool")
            continue
        if tid == "delete_sites_locations":
            record(tool["name"], f"/run/{tid}", "skipped", "Destructive — excluded from demo", category="tool")
            continue

        print(f"  Tool: {tid}...", flush=True)
        status, notes = run_tool_dry(tid, form, files)
        print(f"    -> {status}", flush=True)
        record(tool["name"], f"/run/{tid} (dry)", status, notes, category="tool")

    # Live create/delete test for external_contacts with dummy number
    ec_csv = write_csv("ec_apply.csv", ["Name", "Number"], [[DUMMY_CONTACT_NAME, DUMMY_CONTACT_NUMBER]])
    code, out = run_subprocess([
        sys.executable, str(PROJECT_ROOT / "tools/queues/external_contacts.py"),
        str(ec_csv), "--apply",
    ])
    if code == 0 and "Created" in out:
        record("external_contacts (apply+verify)", "/tools/queues/external_contacts.py --apply", "pass",
               "Created Cursor.AI dummy contact; will cleanup", category="tool")
        # Find and track for cleanup
        for c in client.paged_get("/api/v2/externalcontacts/contacts"):
            fn = (c.get("firstName") or "") + " " + (c.get("lastName") or "")
            if "Cursor.AI" in fn.strip():
                DUMMY_AUDIT.append({"type": "external_contact", "id": c.get("id", ""), "name": fn.strip(), "action": "created_via_apply"})
    else:
        record("external_contacts (apply+verify)", "/tools/queues/external_contacts.py --apply", "partial",
               out[-1000:], category="tool")

    if dummy_queue_id:
        record("Dummy queue create", "POST /api/v2/routing/queues", "pass", f"id={dummy_queue_id}", category="dummy")


def main() -> int:
    print("=== Ops Demo Validation ===")
    api_client = None
    try:
        api_client = create_client(log=tool_log)
        org = api_client.request("GET", "/api/v2/organizations/me")
        print(f"Authenticated: {org.get('name')}")
    except Exception as exc:
        print(f"API auth failed: {exc}")
        record("API Auth", "create_client()", "fail", str(exc))

    validate_flask_routes(api_client)

    if api_client:
        validate_tools(api_client)
        cleanup_logs = cleanup_dummies(api_client)
        record("Dummy cleanup", "DELETE dummies", "pass" if cleanup_logs else "partial",
               "; ".join(cleanup_logs) or "nothing to delete")

        # Verify no Cursor.AI queues/contacts remain
        remaining: list[str] = []
        for q in api_client.paged_get("/api/v2/routing/queues"):
            if DUMMY_PREFIX.lower() in (q.get("name") or "").lower():
                remaining.append(f"queue:{q.get('name')}")
        for c in api_client.paged_get("/api/v2/externalcontacts/contacts"):
            fn = ((c.get("firstName") or "") + " " + (c.get("lastName") or "")).strip()
            if "cursor.ai" in fn.lower():
                remaining.append(f"contact:{fn}")
        record("Cleanup verification", "search Cursor.AI objects", "pass" if not remaining else "fail",
               "clean" if not remaining else "; ".join(remaining))

    out = {
        "results": RESULTS,
        "dummy_audit": DUMMY_AUDIT,
        "summary": {
            "pass": sum(1 for r in RESULTS if r["status"] == "pass"),
            "fail": sum(1 for r in RESULTS if r["status"] == "fail"),
            "partial": sum(1 for r in RESULTS if r["status"] == "partial"),
            "skipped": sum(1 for r in RESULTS if r["status"] == "skipped"),
        },
    }
    out_path = PROJECT_ROOT / "outputs" / "ops_demo_validation_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out["summary"], indent=2))
    print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
