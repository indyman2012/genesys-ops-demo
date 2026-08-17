#!/usr/bin/env python3
"""
Create or update the Genesys Cloud custom role "Roaming Agent".

Grants the minimum telephony permissions needed for the WebRTC Site Picker:
  - list telephony sites (dropdown)
  - view/edit the signed-in user's station association (phone lookup)
  - view/edit managed phones on the roaming sites the agent may select

Default is DRY RUN. Pass --apply to create/update the role in Genesys.

Examples
--------
Preview policies (no API writes):
  python tools/users_roles/create_roaming_agent_role.py --all-sites

Create the role with phone edit on specific sites:
  python tools/users_roles/create_roaming_agent_role.py --apply \\
    --site-ids d91bad08-d7f8-4f90-b75f-c30b4ba023c8,b4ae6b12-8502-4795-92bd-229bcf36e463

Resolve site names from Genesys before creating:
  python tools/users_roles/create_roaming_agent_role.py --apply \\
    --site-names "Headquarters,Center East"

Notes
-----
- Assign this role *in addition to* the user's normal agent license role (Employee / Agent).
- Phone site edit is scoped to the site IDs you pass via --site-ids, --site-names, or --all-sites.
- Requires admin client credentials in client.env (GENESYS_CLIENT_ID / GENESYS_CLIENT_SECRET).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core.cli import add_apply_arg, add_env_arg, create_client, output_path, print_mode_banner, tool_log
from core.genesys_client import GenesysError, find_by_name, safe_str

ROLE_NAME = "Roaming Agent"
ROLE_DESCRIPTION = (
    "Minimal permissions for the Genesys WebRTC Site Picker: view telephony sites, "
    "manage own WebRTC station association, and change WebRTC phone site on approved roaming sites."
)

PHONE_ENTITY_CANDIDATES = (
    "sitesManagedPhones",
    "sitesManagedPhone",
    "managedPhone",
    "phone",
)


def _policy(
    *,
    domain: str,
    entity_name: str,
    actions: list[str],
    site_ids: list[str] | None = None,
) -> dict[str, Any]:
    policy: dict[str, Any] = {
        "domain": domain,
        "entityName": entity_name,
        "actionSet": actions,
        "allowConditions": False,
    }
    if site_ids:
        policy["namedResources"] = site_ids
    return policy


def base_policies() -> list[dict[str, Any]]:
    return [
        _policy(domain="telephony", entity_name="sites", actions=["view"]),
        _policy(
            domain="telephony",
            entity_name="selfStationAssociation",
            actions=["view", "edit"],
        ),
        _policy(domain="telephony", entity_name="station", actions=["disassociateSelf"]),
    ]


def discover_phone_entity(client) -> str:
    payload = client.request(
        "GET",
        "/api/v2/authorization/permissions",
        params={
            "queryType": "domain",
            "query": "telephony",
            "pageSize": 100,
            "pageNumber": 1,
        },
    )
    entities = (payload or {}).get("entities") or []
    available: dict[str, set[str]] = {}
    for block in entities:
        permission_map = (block or {}).get("permissionMap") or {}
        for entity_type, entries in permission_map.items():
            if not isinstance(entries, list):
                continue
            bucket = available.setdefault(str(entity_type), set())
            for entry in entries:
                action = safe_str((entry or {}).get("action")).strip().lower()
                if action:
                    bucket.add(action)

    for candidate in PHONE_ENTITY_CANDIDATES:
        actions = available.get(candidate) or set()
        if {"view", "edit"}.issubset(actions):
            return candidate

    for entity_type, actions in sorted(available.items()):
        if "phone" in entity_type.lower() and {"view", "edit"}.issubset(actions):
            return entity_type

    raise GenesysError(
        "Could not find a telephony phone entity with view+edit in the permissions catalog. "
        f"Checked: {', '.join(PHONE_ENTITY_CANDIDATES)}"
    )


def phone_site_policies(site_ids: list[str], *, phone_entity: str) -> list[dict[str, Any]]:
    if not site_ids:
        return []
    return [
        _policy(
            domain="telephony",
            entity_name=phone_entity,
            actions=["view", "edit"],
            site_ids=site_ids,
        )
    ]


def build_policies(site_ids: list[str], *, phone_entity: str) -> list[dict[str, Any]]:
    return base_policies() + phone_site_policies(site_ids, phone_entity=phone_entity)


def resolve_site_ids(client, *, site_ids: list[str], site_names: list[str], all_sites: bool) -> list[str]:
    ids = [safe_str(value).strip() for value in site_ids if safe_str(value).strip()]
    if all_sites or site_names:
        sites = client.paged_get("/api/v2/telephony/providers/edges/sites", page_size=100)
        if all_sites:
            ids.extend(str(site.get("id")) for site in sites if site.get("id"))
        for name in site_names:
            match = find_by_name(sites, name)
            if not match:
                raise GenesysError(f'Telephony site not found: "{name}"')
            ids.append(str(match["id"]))
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    unique: list[str] = []
    for site_id in ids:
        if site_id and site_id not in seen:
            seen.add(site_id)
            unique.append(site_id)
    return unique


def find_role_by_name(client, name: str) -> dict[str, Any] | None:
    target = name.strip().casefold()
    for role in client.paged_get("/api/v2/authorization/roles", page_size=100):
        if safe_str(role.get("name")).strip().casefold() == target:
            return role
    return None


def create_role(client, name: str, description: str) -> dict[str, Any]:
    return client.request(
        "POST",
        "/api/v2/authorization/roles",
        json_body={"name": name, "description": description},
        expected_statuses=[200, 201],
    )


def update_role(
    client,
    role_id: str,
    name: str,
    description: str,
    permission_policies: list[dict[str, Any]],
) -> dict[str, Any]:
    return client.request(
        "PUT",
        f"/api/v2/authorization/roles/{role_id}",
        json_body={
            "name": name,
            "description": description,
            "permissionPolicies": permission_policies,
        },
    )


def summarize_policies(policies: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for policy in policies:
        actions = ", ".join(policy.get("actionSet") or [])
        entity = policy.get("entityName") or policy.get("entity_name") or "?"
        domain = policy.get("domain") or "?"
        site_count = len(policy.get("namedResources") or [])
        suffix = f" ({site_count} site(s))" if site_count else ""
        lines.append(f"  - {domain}:{entity} -> {actions}{suffix}")
    return lines


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f'Create/update the "{ROLE_NAME}" Genesys role.')
    add_apply_arg(parser)
    add_env_arg(parser)
    parser.add_argument("--role-name", default=ROLE_NAME, help=f'Role name (default: "{ROLE_NAME}")')
    parser.add_argument("--description", default=ROLE_DESCRIPTION, help="Role description")
    parser.add_argument(
        "--site-ids",
        default="",
        help="Comma-separated telephony site IDs where agents may view/edit phones",
    )
    parser.add_argument(
        "--site-names",
        default="",
        help="Comma-separated telephony site names to resolve to IDs",
    )
    parser.add_argument(
        "--all-sites",
        action="store_true",
        help="Grant phone view/edit on every telephony site in the org (use with care)",
    )
    parser.add_argument(
        "--phone-entity",
        default="",
        help="Override telephony permission entity for phone view/edit (auto-detected by default)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output JSON path (default: outputs/roaming_agent_role_preview.json)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    explicit_site_ids = [part.strip() for part in args.site_ids.split(",") if part.strip()]
    explicit_site_names = [part.strip() for part in args.site_names.split(",") if part.strip()]

    if not args.apply:
        phone_entity = args.phone_entity.strip() or "sitesManagedPhones"
        preview_site_ids = explicit_site_ids
        preview = {
            "mode": "DRY_RUN",
            "role_name": args.role_name,
            "description": args.description,
            "phone_entity": phone_entity,
            "site_ids": preview_site_ids,
            "site_names": explicit_site_names,
            "all_sites": bool(args.all_sites),
            "permission_policies": build_policies(preview_site_ids, phone_entity=phone_entity),
            "notes": [
                "Pass --apply with --site-ids, --site-names, and/or --all-sites to create the role.",
                "Assign this role to agents in addition to their normal agent license role.",
                "Without site-scoped phone view/edit, agents can sign in but cannot change phone location.",
            ],
        }
        out = Path(args.output) if args.output else output_path("roaming_agent_role_preview.json")
        out.write_text(json.dumps(preview, indent=2, ensure_ascii=False), encoding="utf-8")
        print_mode_banner(
            f'Preview "{args.role_name}" role',
            apply=False,
            role=args.role_name,
            output=str(out),
        )
        print("Policies:")
        for line in summarize_policies(preview["permission_policies"]):
            print(line)
        if not preview_site_ids and not explicit_site_names and not args.all_sites:
            print("")
            tool_log(
                "warn",
                "No roaming sites specified yet — phone view/edit policy will be empty in this preview.",
            )
            tool_log(
                "warn",
                "Re-run with --site-ids, --site-names, or --all-sites before --apply.",
            )
        print(f"\nPreview saved to {out}")
        return 0

    if not explicit_site_ids and not explicit_site_names and not args.all_sites:
        raise SystemExit(
            "Specify at least one of --site-ids, --site-names, or --all-sites before --apply."
        )

    client = create_client(env_file=args.env_file)
    site_ids = resolve_site_ids(
        client,
        site_ids=explicit_site_ids,
        site_names=explicit_site_names,
        all_sites=bool(args.all_sites),
    )
    if not site_ids:
        raise SystemExit("No telephony site IDs resolved; cannot create Roaming Agent role.")

    phone_entity = args.phone_entity.strip() or discover_phone_entity(client)
    policies = build_policies(site_ids, phone_entity=phone_entity)

    print_mode_banner(
        f'Create/update "{args.role_name}" role',
        apply=True,
        role=args.role_name,
        phone_entity=phone_entity,
        site_count=str(len(site_ids)),
    )
    print("Policies:")
    for line in summarize_policies(policies):
        print(line)

    existing = find_role_by_name(client, args.role_name)
    if existing:
        role_id = str(existing["id"])
        tool_log("info", f'Updating existing role "{args.role_name}" ({role_id})')
        updated = update_role(client, role_id, args.role_name, args.description, policies)
        result = {"action": "updated", "role": updated, "permission_policies": policies}
    else:
        tool_log("info", f'Creating role "{args.role_name}"')
        created = create_role(client, args.role_name, args.description)
        role_id = str(created.get("id") or "")
        if not role_id:
            raise SystemExit("Role creation did not return an id.")
        updated = update_role(client, role_id, args.role_name, args.description, policies)
        result = {"action": "created", "role": updated, "permission_policies": policies}

    out = Path(args.output) if args.output else output_path("roaming_agent_role_result.json")
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    tool_log("ok", f'Done. Role id: {role_id}')
    tool_log("ok", f"Result saved to {out}")
    tool_log(
        "info",
        "Assign this role to agents (Admin > People > user > Roles) in the divisions where they work.",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
