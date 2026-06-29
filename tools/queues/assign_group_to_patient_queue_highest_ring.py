#!/usr/bin/env python3
"""Add a group to the highest bullseye ring on Patient* queues."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json
from typing import Any

from core.cli import add_apply_arg, add_env_arg, create_client, output_path, setup_path, tool_log
from core.genesys_client import find_by_name, safe_str


def build_group_ref(group: dict[str, Any]) -> dict[str, Any]:
    ref: dict[str, Any] = {
        "id": safe_str(group.get("id")),
        "name": safe_str(group.get("name")),
        "type": "GROUP",
    }
    division = group.get("division")
    if isinstance(division, dict):
        ref["division"] = {"id": safe_str(division.get("id")), "name": safe_str(division.get("name"))}
    if group.get("selfUri"):
        ref["selfUri"] = group["selfUri"]
    if group.get("memberCount") is not None:
        ref["memberCount"] = group["memberCount"]
    return ref


def highest_ring_index(rings: list[dict[str, Any]]) -> int:
    last_with_groups = -1
    for index, ring in enumerate(rings):
        if ring.get("memberGroups"):
            last_with_groups = index
    if last_with_groups >= 0:
        return last_with_groups
    return len(rings) - 1 if rings else -1


def build_queue_payload(full: dict[str, Any], group: dict[str, Any], ring_index: int) -> dict[str, Any]:
    bullseye = json.loads(json.dumps(full.get("bullseye") or {}))
    rings = bullseye.get("rings") or []
    if ring_index < 0 or ring_index >= len(rings):
        raise ValueError("Queue does not have a valid target ring")

    target_ring = rings[ring_index]
    member_groups = list(target_ring.get("memberGroups") or [])
    group_id = safe_str(group.get("id"))
    if not any(safe_str(item.get("id")) == group_id for item in member_groups if isinstance(item, dict)):
        member_groups.append(build_group_ref(group))
    target_ring["memberGroups"] = member_groups
    rings[ring_index] = target_ring
    bullseye["rings"] = rings

    payload = {
        "name": full.get("name"),
        "description": full.get("description"),
        "division": full.get("division"),
        "mediaSettings": full.get("mediaSettings"),
        "routingRules": full.get("routingRules"),
        "bullseye": bullseye,
        "acwSettings": full.get("acwSettings"),
        "skillEvaluationMethod": full.get("skillEvaluationMethod"),
        "suppressInQueueCallRecording": full.get("suppressInQueueCallRecording"),
        "autoAnswerOnly": full.get("autoAnswerOnly"),
        "enableTranscription": full.get("enableTranscription"),
        "enableManualAssignment": full.get("enableManualAssignment"),
        "manualAssignment": full.get("manualAssignment"),
        "agentOwnedRouting": full.get("agentOwnedRouting"),
        "callingPartyName": full.get("callingPartyName"),
        "callingPartyNumber": full.get("callingPartyNumber"),
        "defaultScripts": full.get("defaultScripts"),
        "queueFlow": full.get("queueFlow"),
        "scoringMethod": full.get("scoringMethod"),
        "lastAgentRoutingMode": full.get("lastAgentRoutingMode"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Assign group to highest ring on Patient* queues.")
    parser.add_argument("--group", required=True, help="Exact group name")
    parser.add_argument("--output-json", default="assign_group_to_patient_queue_highest_ring_results.json")
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    group = find_by_name(client.paged_get("/api/v2/groups"), args.group)
    if not group:
        raise SystemExit(f"Group not found: {args.group}")
    tool_log("info", f"Using group: {group['name']} ({group['id']})")

    queues = [q for q in client.paged_get("/api/v2/routing/queues") if safe_str(q.get("name")).startswith("Patient")]
    tool_log("info", f"Found {len(queues)} Patient* queues")

    results: list[dict[str, Any]] = []
    for queue in queues:
        queue_id = queue["id"]
        name = queue.get("name")
        full = client.request("GET", f"/api/v2/routing/queues/{queue_id}")
        rings = (full.get("bullseye") or {}).get("rings") or []
        if not rings:
            tool_log("warn", f"Skip (no rings): {name}")
            results.append({"queue_name": name, "queue_id": queue_id, "status": "skipped_no_rings"})
            continue

        ring_index = highest_ring_index(rings)
        target_groups = rings[ring_index].get("memberGroups") or []
        group_id = safe_str(group.get("id"))
        if any(safe_str(item.get("id")) == group_id for item in target_groups if isinstance(item, dict)):
            tool_log("ok", f"Skip (already assigned): {name}")
            results.append({"queue_name": name, "queue_id": queue_id, "status": "already_assigned", "target_ring_number": ring_index + 1})
            continue

        payload = build_queue_payload(full, group, ring_index)
        if args.apply:
            client.request("PUT", f"/api/v2/routing/queues/{queue_id}", json_body=payload)
            tool_log("ok", f"Updated {name} (ring {ring_index + 1})")
            results.append({"queue_name": name, "queue_id": queue_id, "status": "updated", "target_ring_number": ring_index + 1})
        else:
            tool_log("dry", f"Would update {name} (ring {ring_index + 1})")
            results.append({"queue_name": name, "queue_id": queue_id, "status": "dry_run_ready", "target_ring_number": ring_index + 1})

    out = output_path(args.output_json)
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    tool_log("ok", f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
