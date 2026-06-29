#!/usr/bin/env python3
"""
Genesys Cloud Architect Callback SLA Audit - Callback + Call Media Version

This version audits Architect-created callbacks by reading BOTH media resources:

  GET /api/v2/conversations/callbacks/{conversationId}
  GET /api/v2/conversations/calls/{conversationId}

Why:
  The callback resource shows the PureCloud Callback object and assignment.
  The calls resource shows the actual inbound/outbound voice legs.

Classification:
  PASS       outbound call to callback number connected within SLA minutes of agent assignment
  LATE_CALL  outbound call to callback number connected after SLA minutes but within max late window
  NO_CALL    no matching outbound call to callback number found in the call object
  NO_AGENT   no agent assignment/connected time found in the callback object
  NO_NUMBER  no callback number found

Default SLA: 10 minutes
Default max late window: 24 hours

Example:
  python genesys_callback_sla_audit_callback_plus_call.py --start 2026-06-01 --end 2026-06-18 --output callback_audit.csv

Auth:
  Reads client.env by default.

Expected client.env fields:
  GENESYS_CLIENT_ID=...
  GENESYS_CLIENT_SECRET=...
  GENESYS_REGION=us_west_2

Region may also be supplied as us-west-2, usw2, mypurecloud.com, etc.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:  # pragma: no cover
    ZoneInfo = None
    ZoneInfoNotFoundError = Exception

try:
    import purecloudplatformclientv2 as genesys
except ImportError:
    import PureCloudPlatformClientV2 as genesys  # type: ignore


REGION_HOSTS = {
    "us_east_1": "mypurecloud.com",
    "us-east-1": "mypurecloud.com",
    "use1": "mypurecloud.com",
    "mypurecloud.com": "mypurecloud.com",

    "us_east_2": "use2.us-gov-pure.cloud",
    "us-east-2": "use2.us-gov-pure.cloud",
    "use2": "use2.us-gov-pure.cloud",
    "use2.us-gov-pure.cloud": "use2.us-gov-pure.cloud",

    "us_west_2": "usw2.pure.cloud",
    "us-west-2": "usw2.pure.cloud",
    "usw2": "usw2.pure.cloud",
    "usw2.pure.cloud": "usw2.pure.cloud",

    "ca_central_1": "cac1.pure.cloud",
    "ca-central-1": "cac1.pure.cloud",
    "cac1": "cac1.pure.cloud",
    "cac1.pure.cloud": "cac1.pure.cloud",

    "eu_west_1": "mypurecloud.ie",
    "eu-west-1": "mypurecloud.ie",
    "euw1": "mypurecloud.ie",
    "mypurecloud.ie": "mypurecloud.ie",

    "eu_west_2": "euw2.pure.cloud",
    "eu-west-2": "euw2.pure.cloud",
    "euw2": "euw2.pure.cloud",
    "euw2.pure.cloud": "euw2.pure.cloud",

    "eu_central_1": "mypurecloud.de",
    "eu-central-1": "mypurecloud.de",
    "euc1": "mypurecloud.de",
    "mypurecloud.de": "mypurecloud.de",

    "ap_southeast_2": "mypurecloud.com.au",
    "ap-southeast-2": "mypurecloud.com.au",
    "apse2": "mypurecloud.com.au",
    "mypurecloud.com.au": "mypurecloud.com.au",

    "ap_northeast_1": "mypurecloud.jp",
    "ap-northeast-1": "mypurecloud.jp",
    "apne1": "mypurecloud.jp",
    "mypurecloud.jp": "mypurecloud.jp",

    "ap_northeast_2": "apne2.pure.cloud",
    "ap-northeast-2": "apne2.pure.cloud",
    "apne2": "apne2.pure.cloud",
    "apne2.pure.cloud": "apne2.pure.cloud",
}


@dataclass
class AuditRow:
    conversation_id: str
    queue_id: str
    queue_name: str
    callback_number: str
    agent_user_id: str
    agent_name: str
    callback_created_time_utc: str
    agent_alert_time_utc: str
    agent_assigned_time_utc: str
    outbound_start_time_utc: str
    outbound_connected_time_utc: str
    outbound_dnis: str
    outbound_ani: str
    minutes_to_connect: str
    status: str
    disconnect_type: str
    wrapup_code: str
    callback_uri: str
    call_uri: str
    notes: str


def load_env_file(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def normalize_region(region: str) -> str:
    key = (region or "").strip().lower().replace(" ", "")
    host = REGION_HOSTS.get(key)
    if host:
        return host
    if "." in key:
        return key
    raise ValueError(f"Unknown Genesys region '{region}'. Try us_west_2 or usw2.pure.cloud.")


def configure_client(env_file: Path) -> None:
    env = load_env_file(env_file)

    client_id = (
        os.getenv("GENESYS_CLIENT_ID")
        or os.getenv("PURECLOUD_CLIENT_ID")
        or env.get("GENESYS_CLIENT_ID")
        or env.get("PURECLOUD_CLIENT_ID")
        or env.get("CLIENT_ID")
    )
    client_secret = (
        os.getenv("GENESYS_CLIENT_SECRET")
        or os.getenv("PURECLOUD_CLIENT_SECRET")
        or env.get("GENESYS_CLIENT_SECRET")
        or env.get("PURECLOUD_CLIENT_SECRET")
        or env.get("CLIENT_SECRET")
    )
    region = (
        os.getenv("GENESYS_REGION")
        or os.getenv("PURECLOUD_REGION")
        or env.get("GENESYS_REGION")
        or env.get("PURECLOUD_REGION")
        or env.get("REGION")
        or "us_west_2"
    )

    if not client_id or not client_secret:
        raise RuntimeError(
            f"Missing Genesys OAuth credentials. Expected GENESYS_CLIENT_ID and "
            f"GENESYS_CLIENT_SECRET in environment or {env_file}."
        )

    host = normalize_region(region)
    genesys.configuration.host = f"https://api.{host}"

    api_client = genesys.api_client.ApiClient().get_client_credentials_token(
        client_id,
        client_secret,
    )
    genesys.configuration.access_token = api_client.access_token


def get_api_client() -> Any:
    return genesys.ApiClient()


def _call_api_compat(
    client: Any,
    resource_path: str,
    method: str,
    body: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compatibility wrapper for different Genesys SDK versions.

    Important:
      This intentionally does NOT pass async request. Some installed SDK versions
      reject that keyword.
    """
    kwargs = {
        "path_params": {},
        "query_params": [],
        "header_params": {},
        "body": body,
        "post_params": [],
        "files": {},
        "response_type": "object",
        "auth_settings": ["PureCloud OAuth"],
    }

    try:
        result = client.call_api(
            resource_path,
            method,
            _return_http_data_only=True,
            **kwargs,
        )
    except TypeError as exc:
        # Older SDKs may reject _return_http_data_only.
        if "_return_http_data_only" not in str(exc) and "unexpected keyword argument" not in str(exc):
            raise
        result = client.call_api(resource_path, method, **kwargs)

    if isinstance(result, tuple):
        return result[0]
    return result


def api_get(path: str, retries: int = 5) -> Dict[str, Any]:
    client = get_api_client()
    for attempt in range(retries):
        try:
            return _call_api_compat(client, path, "GET")
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status == 429 or status in (500, 502, 503, 504):
                sleep_for = min(30, 2 ** attempt)
                print(f"API GET {path} failed with {status}; retrying in {sleep_for}s...", file=sys.stderr)
                time.sleep(sleep_for)
                continue
            raise
    raise RuntimeError(f"API GET {path} failed after {retries} retries")


def api_post(path: str, body: Dict[str, Any], retries: int = 5) -> Dict[str, Any]:
    client = get_api_client()
    for attempt in range(retries):
        try:
            return _call_api_compat(client, path, "POST", body=body)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status == 429 or status in (500, 502, 503, 504):
                sleep_for = min(30, 2 ** attempt)
                print(f"API POST {path} failed with {status}; retrying in {sleep_for}s...", file=sys.stderr)
                time.sleep(sleep_for)
                continue
            raise
    raise RuntimeError(f"API POST {path} failed after {retries} retries")


def get_local_tz(timezone_name: str):
    if ZoneInfo is None:
        print("WARNING: zoneinfo unavailable; using fixed -04:00 offset.", file=sys.stderr)
        return timezone(timedelta(hours=-4))

    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        print(
            f"WARNING: Timezone '{timezone_name}' not available. "
            f"Using fixed -04:00 offset. For proper timezone support on Windows, run: pip install tzdata",
            file=sys.stderr,
        )
        return timezone(timedelta(hours=-4))


def parse_date_boundary(value: str, tz, is_end: bool) -> datetime:
    """
    Accepts YYYY-MM-DD or an ISO datetime.
    End dates supplied as YYYY-MM-DD are treated as exclusive next midnight.
    """
    value = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        dt = datetime.fromisoformat(value).replace(tzinfo=tz)
        if is_end:
            dt = dt + timedelta(days=1)
        return dt.astimezone(timezone.utc)

    # Accept trailing Z.
    if value.endswith("Z"):
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def fmt_dt(dt: Optional[datetime]) -> str:
    if not dt:
        return ""
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def normalize_phone(value: Optional[str]) -> str:
    if not value:
        return ""
    digits = re.sub(r"\D+", "", value)
    if not digits:
        return ""
    # For NANP, normalize to last 10. This intentionally treats +1NXXNXXXXXX
    # and NXXNXXXXXX as equivalent.
    if len(digits) >= 10:
        return digits[-10:]
    return digits


def first_callback_number(callback_obj: Dict[str, Any]) -> str:
    # Callback numbers can be present on acd/agent/customer participant records.
    for participant in callback_obj.get("participants", []) or []:
        nums = participant.get("callbackNumbers") or []
        if nums:
            return nums[0]

    # Fallback: customer address.
    for participant in callback_obj.get("participants", []) or []:
        if participant.get("purpose") == "customer":
            address = participant.get("address") or participant.get("ani")
            if address:
                return address
    return ""


def find_acd_participant(callback_obj: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    acds = [p for p in callback_obj.get("participants", []) or [] if p.get("purpose") == "acd"]
    if not acds:
        return None
    return min(acds, key=lambda p: p.get("startTime") or "")


def find_agent_assignment(callback_obj: Dict[str, Any]) -> Tuple[Optional[Dict[str, Any]], Optional[datetime], Optional[datetime]]:
    """
    For callback object:
      startAlertingTime = agent began alerting
      connectedTime    = agent accepted/connected to callback object

    We use connectedTime as assigned_time when available.
    """
    agents = [p for p in callback_obj.get("participants", []) or [] if p.get("purpose") == "agent"]
    if not agents:
        return None, None, None

    def sort_key(p):
        return p.get("connectedTime") or p.get("startAlertingTime") or p.get("startTime") or ""

    agent = min(agents, key=sort_key)
    alert_time = parse_dt(agent.get("startAlertingTime") or agent.get("startTime"))
    assigned_time = parse_dt(agent.get("connectedTime") or agent.get("startAlertingTime") or agent.get("startTime"))
    return agent, alert_time, assigned_time


def extract_agent_name(agent_user_id: str, user_cache: Dict[str, str]) -> str:
    if not agent_user_id:
        return ""
    if agent_user_id in user_cache:
        return user_cache[agent_user_id]
    try:
        user = api_get(f"/api/v2/users/{agent_user_id}")
        name = user.get("name") or user.get("email") or agent_user_id
    except Exception:
        name = agent_user_id
    user_cache[agent_user_id] = name
    return name


def callback_created_time(callback_obj: Dict[str, Any]) -> Optional[datetime]:
    acd = find_acd_participant(callback_obj)
    if acd:
        return parse_dt(acd.get("startTime") or acd.get("connectedTime"))
    starts = [parse_dt(p.get("startTime")) for p in callback_obj.get("participants", []) or []]
    starts = [s for s in starts if s]
    return min(starts) if starts else None


def queue_info(callback_obj: Dict[str, Any]) -> Tuple[str, str]:
    acd = find_acd_participant(callback_obj)
    if acd:
        q = acd.get("queue") or {}
        return q.get("id", ""), acd.get("name", "")
    for p in callback_obj.get("participants", []) or []:
        q = p.get("queue") or {}
        if q.get("id"):
            return q.get("id", ""), p.get("name", "")
    return "", ""


def find_call_uri(callback_obj: Dict[str, Any]) -> str:
    for uri in callback_obj.get("otherMediaUris", []) or []:
        if "/conversations/calls/" in uri:
            return uri
    cid = callback_obj.get("id")
    return f"/api/v2/conversations/calls/{cid}" if cid else ""


def find_matching_outbound_call(
    call_obj: Dict[str, Any],
    callback_number: str,
    assigned_time: datetime,
    agent_user_id: str,
    max_late_minutes: int,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """
    Look in the call media object for outbound call legs matching:
      direction outbound
      dnis/address matching callback number
      connectedTime/startTime after assigned_time
      ideally same agent userId when present

    Returns earliest matching connected/start time.
    """
    notes: List[str] = []
    target = normalize_phone(callback_number)
    candidates = []

    for p in call_obj.get("participants", []) or []:
        if p.get("direction") != "outbound":
            continue

        purpose = p.get("purpose") or ""
        # For the agent leg, the DNIS is the customer number.
        # For the customer outbound leg, ANI may be the customer and DNIS may be the outbound ANI,
        # so prefer agent leg but allow customer leg if it clearly matches address/ani.
        possible_numbers = [
            p.get("dnis"),
            p.get("address"),
            p.get("ani"),
        ]
        if target and target not in [normalize_phone(x) for x in possible_numbers if x]:
            continue

        connected = parse_dt(p.get("connectedTime"))
        started = parse_dt(p.get("startTime"))
        event_time = connected or started
        if not event_time:
            continue

        if event_time < assigned_time:
            continue

        minutes = (event_time - assigned_time).total_seconds() / 60.0
        if minutes > max_late_minutes:
            notes.append(f"ignored outbound candidate after max window: {minutes:.2f} minutes")
            continue

        p_user_id = ((p.get("user") or {}).get("id") or "")
        same_agent = bool(agent_user_id and p_user_id == agent_user_id)
        is_agent_leg = p.get("purpose") == "agent"

        candidates.append({
            "participant": p,
            "event_time": event_time,
            "connected_time": connected,
            "start_time": started,
            "minutes": minutes,
            "same_agent": same_agent,
            "is_agent_leg": is_agent_leg,
        })

    if not candidates:
        return None, notes

    # Prefer same-agent agent leg, then any agent leg, then earliest.
    candidates.sort(key=lambda c: (
        0 if c["same_agent"] and c["is_agent_leg"] else
        1 if c["is_agent_leg"] else
        2,
        c["event_time"],
    ))

    best = candidates[0]
    p = dict(best["participant"])
    p["_matched_event_time"] = best["event_time"]
    p["_matched_connected_time"] = best["connected_time"]
    p["_matched_start_time"] = best["start_time"]
    p["_matched_minutes"] = best["minutes"]
    p["_matched_same_agent"] = best["same_agent"]
    if len(candidates) > 1:
        notes.append(f"{len(candidates)} outbound candidates matched; selected best by same-agent/agent-leg/earliest")
    return p, notes


def query_callback_conversation_ids(
    start_utc: datetime,
    end_utc: datetime,
    page_size: int,
    queue_id: Optional[str],
) -> List[str]:
    """
    Use Analytics detail query only as the date-range search/index.
    Then fetch callback/call media resources for each ID.
    """
    ids: List[str] = []
    page = 1

    predicates: List[Dict[str, Any]] = [
        {
            "type": "dimension",
            "dimension": "mediaType",
            "operator": "matches",
            "value": "callback",
        }
    ]

    if queue_id:
        predicates.append({
            "type": "dimension",
            "dimension": "queueId",
            "operator": "matches",
            "value": queue_id,
        })

    body: Dict[str, Any] = {
        "interval": f"{fmt_dt(start_utc)}/{fmt_dt(end_utc)}",
        "order": "asc",
        "orderBy": "conversationStart",
        "paging": {
            "pageSize": page_size,
            "pageNumber": page,
        },
        "segmentFilters": [
            {
                "type": "and",
                "predicates": predicates,
            }
        ],
    }

    while True:
        body["paging"]["pageNumber"] = page
        print(f"Fetching callback conversation ID page {page}...", file=sys.stderr)
        data = api_post("/api/v2/analytics/conversations/details/query", body)
        conversations = data.get("conversations") or []

        if not conversations:
            break

        for conv in conversations:
            cid = conv.get("conversationId") or conv.get("id")
            if cid:
                ids.append(cid)

        if len(conversations) < page_size:
            break
        page += 1

    # De-dupe preserving order.
    seen = set()
    deduped = []
    for cid in ids:
        if cid not in seen:
            seen.add(cid)
            deduped.append(cid)
    return deduped


def audit_conversation(
    conversation_id: str,
    sla_minutes: int,
    max_late_minutes: int,
    user_cache: Dict[str, str],
) -> AuditRow:
    callback_uri = f"/api/v2/conversations/callbacks/{conversation_id}"
    callback_obj = api_get(callback_uri)

    callback_number = first_callback_number(callback_obj)
    qid, qname = queue_info(callback_obj)
    created = callback_created_time(callback_obj)

    agent, alert_time, assigned_time = find_agent_assignment(callback_obj)
    agent_user_id = ""
    agent_name = ""
    disconnect_type = ""
    wrapup_code = ""

    if agent:
        agent_user_id = ((agent.get("user") or {}).get("id") or "")
        agent_name = extract_agent_name(agent_user_id, user_cache) if agent_user_id else ""
        disconnect_type = agent.get("disconnectType") or ""
        wrapup_code = ((agent.get("wrapup") or {}).get("code") or "")

    call_uri = find_call_uri(callback_obj)

    notes: List[str] = []

    if not callback_number:
        return AuditRow(
            conversation_id=conversation_id,
            queue_id=qid,
            queue_name=qname,
            callback_number="",
            agent_user_id=agent_user_id,
            agent_name=agent_name,
            callback_created_time_utc=fmt_dt(created),
            agent_alert_time_utc=fmt_dt(alert_time),
            agent_assigned_time_utc=fmt_dt(assigned_time),
            outbound_start_time_utc="",
            outbound_connected_time_utc="",
            outbound_dnis="",
            outbound_ani="",
            minutes_to_connect="",
            status="NO_NUMBER",
            disconnect_type=disconnect_type,
            wrapup_code=wrapup_code,
            callback_uri=callback_uri,
            call_uri=call_uri,
            notes="No callback number found",
        )

    if not assigned_time:
        return AuditRow(
            conversation_id=conversation_id,
            queue_id=qid,
            queue_name=qname,
            callback_number=callback_number,
            agent_user_id=agent_user_id,
            agent_name=agent_name,
            callback_created_time_utc=fmt_dt(created),
            agent_alert_time_utc=fmt_dt(alert_time),
            agent_assigned_time_utc="",
            outbound_start_time_utc="",
            outbound_connected_time_utc="",
            outbound_dnis="",
            outbound_ani="",
            minutes_to_connect="",
            status="NO_AGENT",
            disconnect_type=disconnect_type,
            wrapup_code=wrapup_code,
            callback_uri=callback_uri,
            call_uri=call_uri,
            notes="No agent connected/assigned time found on callback object",
        )

    try:
        call_obj = api_get(call_uri)
    except Exception as exc:
        return AuditRow(
            conversation_id=conversation_id,
            queue_id=qid,
            queue_name=qname,
            callback_number=callback_number,
            agent_user_id=agent_user_id,
            agent_name=agent_name,
            callback_created_time_utc=fmt_dt(created),
            agent_alert_time_utc=fmt_dt(alert_time),
            agent_assigned_time_utc=fmt_dt(assigned_time),
            outbound_start_time_utc="",
            outbound_connected_time_utc="",
            outbound_dnis="",
            outbound_ani="",
            minutes_to_connect="",
            status="NO_CALL",
            disconnect_type=disconnect_type,
            wrapup_code=wrapup_code,
            callback_uri=callback_uri,
            call_uri=call_uri,
            notes=f"Failed to fetch call media object: {exc}",
        )

    match, match_notes = find_matching_outbound_call(
        call_obj=call_obj,
        callback_number=callback_number,
        assigned_time=assigned_time,
        agent_user_id=agent_user_id,
        max_late_minutes=max_late_minutes,
    )
    notes.extend(match_notes)

    if not match:
        return AuditRow(
            conversation_id=conversation_id,
            queue_id=qid,
            queue_name=qname,
            callback_number=callback_number,
            agent_user_id=agent_user_id,
            agent_name=agent_name,
            callback_created_time_utc=fmt_dt(created),
            agent_alert_time_utc=fmt_dt(alert_time),
            agent_assigned_time_utc=fmt_dt(assigned_time),
            outbound_start_time_utc="",
            outbound_connected_time_utc="",
            outbound_dnis="",
            outbound_ani="",
            minutes_to_connect="",
            status="NO_CALL",
            disconnect_type=disconnect_type,
            wrapup_code=wrapup_code,
            callback_uri=callback_uri,
            call_uri=call_uri,
            notes="No outbound call leg to callback number found after agent assignment",
        )

    minutes = float(match.get("_matched_minutes"))
    status = "PASS" if minutes <= sla_minutes else "LATE_CALL"

    if not match.get("_matched_same_agent"):
        notes.append("matched outbound leg was not same agent userId, or userId was unavailable")

    return AuditRow(
        conversation_id=conversation_id,
        queue_id=qid,
        queue_name=qname,
        callback_number=callback_number,
        agent_user_id=agent_user_id,
        agent_name=agent_name,
        callback_created_time_utc=fmt_dt(created),
        agent_alert_time_utc=fmt_dt(alert_time),
        agent_assigned_time_utc=fmt_dt(assigned_time),
        outbound_start_time_utc=fmt_dt(match.get("_matched_start_time")),
        outbound_connected_time_utc=fmt_dt(match.get("_matched_connected_time")),
        outbound_dnis=match.get("dnis") or "",
        outbound_ani=match.get("ani") or "",
        minutes_to_connect=f"{minutes:.2f}",
        status=status,
        disconnect_type=disconnect_type,
        wrapup_code=wrapup_code,
        callback_uri=callback_uri,
        call_uri=call_uri,
        notes="; ".join(notes),
    )


def write_csv(path: Path, rows: List[AuditRow]) -> None:
    fieldnames = list(asdict(rows[0]).keys()) if rows else list(AuditRow.__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def print_summary(rows: List[AuditRow]) -> None:
    total = len(rows)
    counts: Dict[str, int] = {}
    minutes: List[float] = []
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
        if r.minutes_to_connect:
            try:
                minutes.append(float(r.minutes_to_connect))
            except ValueError:
                pass

    print("\n=== Callback SLA Summary ===")
    print(f"Callbacks audited: {total}")
    for status in ["PASS", "LATE_CALL", "NO_CALL", "NO_AGENT", "NO_NUMBER"]:
        count = counts.get(status, 0)
        pct = (count / total * 100.0) if total else 0
        print(f"{status:<11}: {count:6d} ({pct:5.1f}%)")

    if minutes:
        avg = statistics.mean(minutes)
        med = statistics.median(minutes)
        longest = max(minutes)
        if len(minutes) >= 20:
            p95 = statistics.quantiles(minutes, n=100)[94]
        else:
            p95 = sorted(minutes)[int(round((len(minutes) - 1) * 0.95))]
        print()
        print(f"Average minutes to callback: {avg:.2f}")
        print(f"Median minutes to callback:  {med:.2f}")
        print(f"95th percentile:             {p95:.2f}")
        print(f"Longest matched callback:    {longest:.2f}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit Architect callback SLA using callback object plus paired calls object.",
    )
    parser.add_argument("--start", required=True, help="Start date/datetime. YYYY-MM-DD is interpreted in --timezone.")
    parser.add_argument("--end", required=True, help="End date/datetime. YYYY-MM-DD is exclusive next midnight in --timezone.")
    parser.add_argument("--output", default="callback_audit_callback_plus_call.csv", help="Output CSV path.")
    parser.add_argument("--env-file", default="client.env", help="Path to client.env.")
    parser.add_argument("--timezone", default="America/New_York", help="Timezone for date-only inputs.")
    parser.add_argument("--sla-minutes", type=int, default=10, help="PASS threshold in minutes after agent assignment.")
    parser.add_argument("--max-late-minutes", type=int, default=1440, help="Max minutes to still count as LATE_CALL.")
    parser.add_argument("--page-size", type=int, default=100, help="Analytics detail query page size.")
    parser.add_argument("--queue-id", default="", help="Optional queue ID filter.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max conversations to process for testing.")
    parser.add_argument("--debug-json-dir", default="", help="Optional folder to save callback/call JSON for each conversation.")
    args = parser.parse_args()

    tz = get_local_tz(args.timezone)
    start_utc = parse_date_boundary(args.start, tz, is_end=False)
    end_utc = parse_date_boundary(args.end, tz, is_end=True)

    configure_client(Path(args.env_file))

    print(f"Searching callbacks from {fmt_dt(start_utc)} to {fmt_dt(end_utc)}...", file=sys.stderr)
    conversation_ids = query_callback_conversation_ids(
        start_utc=start_utc,
        end_utc=end_utc,
        page_size=args.page_size,
        queue_id=args.queue_id or None,
    )

    if args.limit and args.limit > 0:
        conversation_ids = conversation_ids[: args.limit]

    print(f"Found {len(conversation_ids)} callback conversations.", file=sys.stderr)

    rows: List[AuditRow] = []
    user_cache: Dict[str, str] = {}

    debug_dir = Path(args.debug_json_dir) if args.debug_json_dir else None
    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)

    for idx, cid in enumerate(conversation_ids, start=1):
        if idx == 1 or idx % 25 == 0 or idx == len(conversation_ids):
            print(f"Auditing {idx}/{len(conversation_ids)}: {cid}", file=sys.stderr)

        try:
            row = audit_conversation(
                conversation_id=cid,
                sla_minutes=args.sla_minutes,
                max_late_minutes=args.max_late_minutes,
                user_cache=user_cache,
            )
            rows.append(row)
        except Exception as exc:
            rows.append(AuditRow(
                conversation_id=cid,
                queue_id="",
                queue_name="",
                callback_number="",
                agent_user_id="",
                agent_name="",
                callback_created_time_utc="",
                agent_alert_time_utc="",
                agent_assigned_time_utc="",
                outbound_start_time_utc="",
                outbound_connected_time_utc="",
                outbound_dnis="",
                outbound_ani="",
                minutes_to_connect="",
                status="ERROR",
                disconnect_type="",
                wrapup_code="",
                callback_uri=f"/api/v2/conversations/callbacks/{cid}",
                call_uri=f"/api/v2/conversations/calls/{cid}",
                notes=f"{type(exc).__name__}: {exc}",
            ))

        # Write periodically so a long run still leaves useful output if interrupted.
        if idx % 100 == 0:
            write_csv(Path(args.output), rows)

    write_csv(Path(args.output), rows)
    print_summary(rows)
    print(f"\nWrote: {Path(args.output).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
