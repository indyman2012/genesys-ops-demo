#!/usr/bin/env python3
"""Ask Genesys — read-only natural-language router (regex/keyword v1, no LLM).

Shared by the web UI (app/ask_genesys/routes.py) and the CLI:

    python tools/reporting/ask_genesys_router.py "When was the last time 9547909709 called us?"

Supported intents (all GET / analytics-query only; nothing here mutates Genesys):
  * last_inbound_call  — phone number in prompt -> most recent inbound call(s)
  * user_queues_joined — "what queues is <name|email> live on"
  * help               — example prompts
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.genesys_client import safe_str
from core.lookups import find_user_by_email, get_all_queues
from tools.reporting.inbound_cdr_by_ani import CSV_FIELDS as CDR_CSV_FIELDS
from tools.reporting.inbound_cdr_by_ani import conversation_to_row, phone_variants

try:  # zoneinfo needs the tzdata package on Windows; fall back to fixed offset.
    from zoneinfo import ZoneInfo

    _EASTERN: Any = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _EASTERN = timezone(timedelta(hours=-4))

DEFAULT_LOOKBACK_DAYS = 90
MAX_LOOKBACK_DAYS = 365
WINDOW_DAYS = 30  # Genesys analytics details query max interval is ~31 days
MAX_CALL_ROWS = 10

HELP_EXAMPLES = [
    "When was the last time 9547909709 called us?",
    "Last inbound call from (954) 790-9709 in the last 180 days",
    "What queues is Jane Doe live on?",
    "Which queues is jane.doe@archwell.com joined to?",
    "Show all queues Jane Doe is a member of",
]

# NANP: area code and exchange must start 2-9.
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?1[\s.\-]?)?\(?([2-9]\d{2})\)?[\s.\-]?([2-9]\d{2})[\s.\-]?(\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(r"[\w.+'\-]+@[\w\-]+(?:\.[\w\-]+)+")
_LOOKBACK_RE = re.compile(r"\b(?:last|past|previous|within(?:\s+the)?)\s+(\d+)?\s*(day|week|month|year)s?\b", re.I)
_UNIT_DAYS = {"day": 1, "week": 7, "month": 30, "year": 365}

_NAME_PATTERNS = [
    re.compile(
        r"queues?\s+(?:is|are|does|has|did)\s+(?P<name>.+?)\s+(?:currently\s+)?"
        r"(?:live|joined|logged|signed|on\b|in\b|a\s+member|member|belong|assigned|active)",
        re.I,
    ),
    re.compile(r"(?:queues?|memberships?)\s+(?:for|of)\s+(?P<name>.+?)(?:\s+(?:live|joined|on|in)\b|[?.!]|$)", re.I),
    re.compile(r"(?P<name>[\w.'\- ]+?)['\u2019]s\s+(?:joined\s+|live\s+)?queues?", re.I),
    re.compile(
        r"\b(?:is|does)\s+(?P<name>.+?)\s+(?:live|joined|a\s+member|member)\s+(?:on|of|in|to)\s+(?:any\s+|which\s+)?queues?",
        re.I,
    ),
]
_NAME_STRIP = re.compile(r"^(?:user|agent|employee|person)\s+|^[\"'\u201c\u201d]+|[\"'\u201c\u201d?.!,]+$", re.I)
_LEAD_WORDS = re.compile(r"^(?:what|which|show|list|me|all|the|queues|queue|is|are|does|do)\s+", re.I)


@dataclass
class ParsedIntent:
    intent: str  # last_inbound_call | user_queues_joined | help
    prompt: str
    phone: str = ""  # 10 digits
    user_query: str = ""  # email or display name
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    include_unjoined: bool = False
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- parsing


def extract_phone(prompt: str) -> str:
    match = _PHONE_RE.search(prompt or "")
    return "".join(match.groups()) if match else ""


def extract_lookback_days(prompt: str) -> int:
    match = _LOOKBACK_RE.search(prompt or "")
    if not match:
        return DEFAULT_LOOKBACK_DAYS
    count = int(match.group(1) or 1)
    days = count * _UNIT_DAYS[match.group(2).lower()]
    return max(1, min(days, MAX_LOOKBACK_DAYS))


def extract_user_query(prompt: str) -> str:
    email = _EMAIL_RE.search(prompt or "")
    if email:
        return email.group(0)
    for pattern in _NAME_PATTERNS:
        match = pattern.search(prompt or "")
        if not match:
            continue
        name = _NAME_STRIP.sub("", match.group("name")).strip()
        while True:
            stripped = _LEAD_WORDS.sub("", name)
            if stripped == name:
                break
            name = stripped
        if name:
            return name.strip()
    return ""


def parse_prompt(prompt: str) -> ParsedIntent:
    text = (prompt or "").strip()
    lowered = text.lower()
    if not text or re.fullmatch(r"(help|examples?|\?|what can you do\??|what can i ask\??)", lowered):
        return ParsedIntent(intent="help", prompt=text)

    phone = extract_phone(text)
    if phone:
        return ParsedIntent(
            intent="last_inbound_call",
            prompt=text,
            phone=phone,
            lookback_days=extract_lookback_days(text),
        )

    if "queue" in lowered:
        user_query = extract_user_query(text)
        if user_query:
            include_unjoined = bool(re.search(r"\b(member|belong|assigned|all queues|every queue)\b", lowered)) and not re.search(
                r"\b(live|joined|logged)\b", lowered
            )
            return ParsedIntent(
                intent="user_queues_joined",
                prompt=text,
                user_query=user_query,
                include_unjoined=include_unjoined,
            )

    return ParsedIntent(intent="help", prompt=text, notes=["Could not match that to a supported question."])


# --------------------------------------------------------------------------- helpers


def _fmt_phone(digits: str) -> str:
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"


def _parse_utc_label(value: str) -> datetime | None:
    try:
        return datetime.strptime(value.replace(" UTC", ""), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    except (ValueError, AttributeError):
        return None


def _fmt_eastern(dt: datetime) -> str:
    local = dt.astimezone(_EASTERN)
    return local.strftime("%a %b %d, %Y %I:%M %p ").replace(" 0", " ") + (local.tzname() or "ET")


def _ago(dt: datetime, now: datetime) -> str:
    seconds = max(0, int((now - dt).total_seconds()))
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{seconds // 60} minutes ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''} ago"


def _fmt_secs(value: Any) -> str:
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return "0s"
    minutes, seconds = divmod(total, 60)
    return f"{minutes}m {seconds:02d}s" if minutes else f"{seconds}s"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _query_recent_inbound(client, interval: str, variants: list[str], *, limit: int) -> list[dict[str, Any]]:
    """Newest-first inbound voice conversations whose ANI matches any variant (single page)."""
    def run(ani_predicates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        body = {
            "order": "desc",
            "orderBy": "conversationStart",
            "interval": interval,
            "paging": {"pageSize": limit, "pageNumber": 1},
            "segmentFilters": [
                {"type": "or", "predicates": ani_predicates},
                {
                    "type": "and",
                    "predicates": [
                        {"type": "dimension", "dimension": "mediaType", "operator": "matches", "value": "voice"},
                        {"type": "dimension", "dimension": "direction", "operator": "matches", "value": "inbound"},
                    ],
                },
            ],
        }
        resp = client.request("POST", "/api/v2/analytics/conversations/details/query", json_body=body)
        return resp.get("conversations") or []

    def ani_pred(value: str) -> dict[str, Any]:
        return {"type": "dimension", "dimension": "ani", "operator": "matches", "value": value}

    try:
        return run([ani_pred(v) for v in variants])
    except Exception:
        # Fallback: one request per variant (matches inbound_cdr_by_ani.py behaviour).
        merged: dict[str, dict[str, Any]] = {}
        for variant in variants:
            try:
                for conv in run([ani_pred(variant)]):
                    merged[conv.get("conversationId") or conv.get("id") or str(len(merged))] = conv
            except Exception:
                continue
        return list(merged.values())


def _fill_agent_names(client, rows: list[dict[str, str]]) -> None:
    """Analytics participants only carry userId; resolve display name/email (read-only GET)."""
    cache: dict[str, tuple[str, str]] = {}
    for row in rows:
        uid = row.get("agentUserId") or ""
        if not uid or row.get("agentName"):
            continue
        if uid not in cache:
            try:
                user = client.request("GET", f"/api/v2/users/{uid}")
                cache[uid] = (safe_str(user.get("name")), safe_str(user.get("email")))
            except Exception:
                cache[uid] = ("", "")
        row["agentName"], row["agentEmail"] = cache[uid]


# --------------------------------------------------------------------------- intents


def answer_last_inbound_call(client, parsed: ParsedIntent) -> dict[str, Any]:
    digits = parsed.phone
    normalized = f"+1{digits}"
    variants = phone_variants(digits)
    now = datetime.now(timezone.utc)
    horizon = now - timedelta(days=parsed.lookback_days)
    windows = math.ceil(parsed.lookback_days / WINDOW_DAYS)

    convs: list[dict[str, Any]] = []
    for index in range(windows):
        end = now - timedelta(days=WINDOW_DAYS * index)
        start = max(end - timedelta(days=WINDOW_DAYS), horizon)
        interval = f"{_iso(start)}/{_iso(end)}"
        convs = _query_recent_inbound(client, interval, variants, limit=25)
        if convs:
            break

    queue_names = {}
    rows: list[dict[str, str]] = []
    if convs:
        queue_names = {(q.get("id") or ""): (q.get("name") or "") for q in get_all_queues(client)}
        for conv in convs:
            row = conversation_to_row(
                conv,
                queue_names=queue_names,
                search_mode="ask-genesys-inbound-ani",
                normalized_target=normalized,
                inbound_only=True,
            )
            if row:
                rows.append(row)
        rows.sort(key=lambda item: item.get("conversationStartUtc") or "", reverse=True)
        rows = rows[:MAX_CALL_ROWS]
        _fill_agent_names(client, rows)

    pretty = _fmt_phone(digits)
    if not rows:
        return {
            "ok": True,
            "intent": parsed.intent,
            "answer": f"No inbound calls from {pretty} were found in the last {parsed.lookback_days} days.",
            "rows": [],
            "csv_fields": CDR_CSV_FIELDS,
            "meta": {"phone": normalized, "lookback_days": parsed.lookback_days},
            "tips": [
                "Try a longer window, e.g. “... in the last 365 days”.",
                "Only inbound voice calls are searched; the number must have reached the contact center.",
            ],
        }

    top = rows[0]
    when = _parse_utc_label(top["conversationStartUtc"])
    when_text = f"{_fmt_eastern(when)} ({_ago(when, now)})" if when else top["conversationStartLocal"]
    queue = top["queueName"] or ""
    agent = top["agentName"] or ""
    parts = [f"The last inbound call from {pretty} was {when_text}."]
    parts.append(f"Direction: {top['direction'] or 'inbound'}.")
    parts.append(f"Queue: {queue}." if queue else "Queue: none (the call did not reach a queue).")
    if agent:
        parts.append(f"Agent: {agent}" + (f" ({top['agentEmail']})" if top["agentEmail"] else "") + ".")
    elif top["agentUserId"]:
        parts.append(f"Agent: user {top['agentUserId']} (name could not be resolved).")
    else:
        parts.append("Agent: none recorded (not answered by an agent).")
    parts.append(
        f"Outcome: {top['outcome']}; talk {_fmt_secs(top['talkSec'])}, total {_fmt_secs(top['durationSec'])}."
    )
    if top["dnis"]:
        parts.append(f"Number dialed: {top['dnis']}.")
    return {
        "ok": True,
        "intent": parsed.intent,
        "answer": " ".join(parts),
        "rows": rows,
        "csv_fields": CDR_CSV_FIELDS,
        "meta": {
            "phone": normalized,
            "lookback_days": parsed.lookback_days,
            "rows_returned": len(rows),
            "conversation_id": top["conversationId"],
        },
    }


def _search_users(client, query: str) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    tokens = [t for t in re.split(r"\s+", query.strip()) if t]
    terms = [query.strip()] + ([tokens[-1]] if len(tokens) > 1 else [])
    for term in terms:
        body = {"query": [{"type": "CONTAINS", "fields": ["name"], "value": term}], "pageSize": 25, "pageNumber": 1}
        try:
            payload = client.request("POST", "/api/v2/users/search", json_body=body)
        except Exception:
            continue
        for user in (payload or {}).get("results") or []:
            if user.get("id"):
                found[user["id"]] = user
        if found:
            break
    wanted = [t.casefold() for t in tokens]
    matches = [u for u in found.values() if all(t in safe_str(u.get("name")).casefold() for t in wanted)]
    exact = [u for u in matches if safe_str(u.get("name")).casefold() == query.strip().casefold()]
    pool = exact or matches
    active = [u for u in pool if safe_str(u.get("state")).lower() in ("", "active")]
    return active or pool


def answer_user_queues(client, parsed: ParsedIntent) -> dict[str, Any]:
    query = parsed.user_query
    if "@" in query:
        user = find_user_by_email(client, query)
        candidates = [user]
    else:
        candidates = _search_users(client, query)

    if not candidates:
        return {
            "ok": False,
            "intent": parsed.intent,
            "answer": f"No Genesys user found matching “{query}”. Try the full name or email address.",
            "rows": [],
            "csv_fields": [],
            "meta": {"user_query": query},
        }
    if len(candidates) > 1:
        options = [
            {"name": safe_str(u.get("name")), "email": safe_str(u.get("email")), "userId": safe_str(u.get("id"))}
            for u in candidates[:10]
        ]
        return {
            "ok": False,
            "intent": parsed.intent,
            "answer": f"“{query}” matches {len(candidates)} users. Re-ask with an email address to be specific.",
            "rows": options,
            "csv_fields": ["name", "email", "userId"],
            "meta": {"user_query": query, "ambiguous": True},
        }

    user = candidates[0]
    user_id = safe_str(user.get("id"))
    name = safe_str(user.get("name")) or query
    memberships = client.paged_get(f"/api/v2/users/{user_id}/queues")
    rows = [
        {
            "queueName": safe_str(q.get("name")),
            "queueId": safe_str(q.get("id")),
            "joined": "true" if q.get("joined") is True else "false",
        }
        for q in memberships
    ]
    rows.sort(key=lambda r: r["queueName"].lower())
    joined = [r for r in rows if r["joined"] == "true"]
    shown = rows if parsed.include_unjoined else joined
    names = ", ".join(r["queueName"] for r in shown[:15])
    more = f" (+{len(shown) - 15} more)" if len(shown) > 15 else ""
    if parsed.include_unjoined:
        answer = f"{name} is a member of {len(rows)} queue(s); {len(joined)} are currently joined (live)."
    else:
        answer = f"{name} is joined (live) on {len(joined)} queue(s)" + (f": {names}{more}." if joined else ".")
        if len(rows) > len(joined):
            answer += f" ({len(rows) - len(joined)} more memberships are not joined.)"
    return {
        "ok": True,
        "intent": parsed.intent,
        "answer": answer,
        "rows": shown,
        "csv_fields": ["queueName", "queueId", "joined"],
        "meta": {
            "user": name,
            "user_id": user_id,
            "email": safe_str(user.get("email")),
            "memberships_total": len(rows),
            "joined_total": len(joined),
        },
    }


def help_result(parsed: ParsedIntent) -> dict[str, Any]:
    lead = parsed.notes[0] + " " if parsed.notes else ""
    return {
        "ok": not parsed.notes,
        "intent": "help",
        "answer": lead + "Ask Genesys answers a few read-only questions. Try one of the examples below.",
        "rows": [],
        "csv_fields": [],
        "examples": HELP_EXAMPLES,
        "meta": {},
    }


def answer_prompt(client_or_factory, prompt: str) -> dict[str, Any]:
    """Parse + answer. `client_or_factory` may be a GenesysClient or a zero-arg callable returning one."""
    parsed = parse_prompt(prompt)
    if parsed.intent == "help":
        result = help_result(parsed)
    else:
        client = client_or_factory() if callable(client_or_factory) else client_or_factory
        if parsed.intent == "last_inbound_call":
            result = answer_last_inbound_call(client, parsed)
        else:
            result = answer_user_queues(client, parsed)
    result["parsed"] = asdict(parsed)
    result["read_only"] = True
    return result


# --------------------------------------------------------------------------- CLI


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask Genesys (read-only natural-language query).")
    parser.add_argument("prompt", nargs="*", help="Question text")
    parser.add_argument("--json", action="store_true", help="Print raw JSON result")
    parser.add_argument("--parse-only", action="store_true", help="Show the parsed intent without calling Genesys")
    parser.add_argument("--env-file", default=None, help="Optional env file path (default: client.env)")
    args = parser.parse_args()
    prompt = " ".join(args.prompt).strip()

    if args.parse_only:
        print(json.dumps(asdict(parse_prompt(prompt)), indent=2))
        return 0

    from core.cli import create_client, tool_log

    result = answer_prompt(lambda: create_client(log=tool_log, env_file=args.env_file), prompt)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
        return 0
    print(result["answer"])
    for example in result.get("examples") or []:
        print(f"  - {example}")
    summary_keys = {
        "last_inbound_call": ["conversationStartLocal", "queueName", "agentName", "outcome"],
        "user_queues_joined": ["queueName", "joined"],
    }.get(result.get("intent"), ["name", "email"])
    for row in result.get("rows") or []:
        print("  " + " | ".join(str(row.get(k, "")) for k in summary_keys))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
