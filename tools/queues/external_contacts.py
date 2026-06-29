#!/usr/bin/env python3
"""Create missing external contacts from CSV."""
from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
from pathlib import Path

from core.cli import add_apply_arg, add_env_arg, create_client, read_csv, setup_path, tool_log, write_jsonl
from core.genesys_client import ensure_e164, normalize_number, safe_str


def contact_numbers(contact: dict) -> list[str]:
    numbers: list[str] = []
    for field in ("workPhone", "cellPhone", "homePhone", "otherPhone", "fax"):
        obj = contact.get(field)
        if isinstance(obj, dict):
            for key in ("display", "e164", "address", "number", "value"):
                number = normalize_number(obj.get(key))
                if number:
                    numbers.append(number)
    return numbers


def main() -> int:
    setup_path()
    parser = argparse.ArgumentParser(description="Create missing external contacts from CSV.")
    parser.add_argument("csv_file", help="CSV with Name, Number columns")
    add_apply_arg(parser)
    add_env_arg(parser)
    args = parser.parse_args()

    client = create_client(log=tool_log, env_file=args.env_file)
    index = {
        number: contact
        for contact in client.paged_get("/api/v2/externalcontacts/contacts")
        for number in contact_numbers(contact)
    }

    for row in read_csv(Path(args.csv_file)):
        name = row.get("Name", "")
        number = row.get("Number", "")
        norm = normalize_number(number)
        if norm in index:
            tool_log("ok", f"Exists: {number}")
            continue
        body: dict = {"firstName": name.split(" ", 1)[0], "workPhone": {"display": ensure_e164(number)}}
        if " " in name:
            body["lastName"] = name.split(" ", 1)[1]
        if args.apply:
            created = client.request("POST", "/api/v2/externalcontacts/contacts", json_body=body)
            tool_log("ok", f"Created {number} -> {created.get('id')}")
        else:
            write_jsonl("external_contacts_payloads.jsonl", body)
            tool_log("dry", f"Would create {name} {number}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
