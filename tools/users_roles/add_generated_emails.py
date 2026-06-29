#!/usr/bin/env python3
"""
Add generated email addresses as a third column to a CSV or XLSX file.

v4 pattern fixes
----------------
- Ignore rows where site/location == NVLWOO
- Ignore obvious non-user / "do not use" rows
- Remove trailing notes in parentheses
- Preserve hyphenated surnames
- Preserve apostrophe letters by removing apostrophes only
- Ignore trailing suffixes (Jr, Sr, II, III, IV, V)
- Ignore single-letter middle initials
- Join multipart surnames after the first name
- Strip common trailing clinical credentials / title fragments

Examples
--------
Amy Costner-Waldau      -> amy.costner-waldau@archwellhealth.com
Rebecca O'Rourke        -> rebecca.orourke@archwellhealth.com
John A Smith            -> john.smith@archwellhealth.com
Maria De La Cruz        -> maria.delacruz@archwellhealth.com
Tisha Dickson Hickson   -> tisha.dicksonhickson@archwellhealth.com
Ramon Chedes Jr         -> ramon.chedes@archwellhealth.com
"""

from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from pathlib import Path
from typing import List, Tuple

import openpyxl


DEFAULT_DOMAIN = "archwellhealth.com"
SKIP_SITE = "NVLWOO"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}

# Standalone credential/title tokens to strip from the tail.
CREDENTIAL_TOKENS = {
    "aprn", "msn", "rn", "pa", "np", "md", "do", "ag", "bc", "f", "c", "d", "ar",
    "fnp", "fnp-c", "pmhnp", "pmhnp-bc", "agpcnp", "agpcnp-c"
}

# Regex fragments for non-person / do-not-use rows.
NON_PERSON_PATTERNS = [
    r"\bdo not use\b",
    r"\bnot use\b",
    r"\bmaybe nt use\b",
    r"\bmaybe not use\b",
    r"\btask\d+\b",
    r"\binc\d+\b",
    r"\bnurse station\b",
    r"\bgetting .* calls\b",
    r"\bspam calls\b",
    r"\bharra?s+s?ing calls\b",
    r"\bmasking correctly\b",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add generated email addresses as a third column.")
    parser.add_argument("input_file", help="Source CSV or XLSX file")
    parser.add_argument("--sheet-name", default="", help="Optional sheet name when reading XLSX")
    parser.add_argument("--domain", default=DEFAULT_DOMAIN, help=f"Email domain (default: {DEFAULT_DOMAIN})")
    parser.add_argument("--output", default="", help="Optional output file path")
    return parser.parse_args()


def default_output_path(input_path: Path) -> Path:
    stem = input_path.stem
    suffix = input_path.suffix.lower()
    if suffix in {".xlsx", ".xlsm"}:
        return input_path.with_name(f"{stem}_with_emails.xlsx")
    if suffix == ".csv":
        return input_path.with_name(f"{stem}_with_emails.csv")
    return input_path.with_name(f"{stem}_with_emails{suffix}")


def strip_accents(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")


def clean_token(token: str) -> str:
    token = re.sub(r"[^A-Za-z0-9-]", "", token).lower()
    token = re.sub(r"-{2,}", "-", token)
    # keep internal hyphens, only strip whitespace
    return token.strip()


def is_non_person_row(person_name: str) -> bool:
    s = person_name.lower()
    return any(re.search(pattern, s, re.I) for pattern in NON_PERSON_PATTERNS)


def strip_parenthetical_notes(name: str) -> str:
    # remove trailing parenthetical notes repeatedly
    s = name
    while True:
        new_s = re.sub(r"\s*\([^)]*\)\s*$", "", s).strip()
        if new_s == s:
            break
        s = new_s
    return s


def strip_trailing_credentials(parts: List[str]) -> List[str]:
    """
    Remove credential/title fragments from the tail, including split hyphen forms like:
    F -BC, A -BC, D -C, AR F -BC

    Important:
    - Do NOT treat ordinary hyphenated surnames as credentials.
    """
    cleaned = parts[:]

    def is_hyphen_credential(token: str) -> bool:
        if not re.fullmatch(r"[a-z]+-[a-z]+", token):
            return False
        left, right = token.split("-", 1)
        # Only strip when the token looks credential-like, not surname-like.
        if token in CREDENTIAL_TOKENS:
            return True
        if left in CREDENTIAL_TOKENS or right in CREDENTIAL_TOKENS:
            return True
        # Common credential shapes are very short fragments like f-bc, pa-c, d-c, ag-c.
        return len(left) <= 3 and len(right) <= 3

    while len(cleaned) > 1:
        tail = cleaned[-1]

        # Exact tail token match
        if tail in CREDENTIAL_TOKENS:
            cleaned.pop()
            continue

        # Tail token like pa-c, f-bc, d-c, c-l, ag-c — but not real surnames
        if is_hyphen_credential(tail):
            cleaned.pop()
            continue

        # Split hyphen credential fragments at the end, e.g. ["f", "-bc"] or ["d", "-c"]
        if re.fullmatch(r"-[a-z]+", tail):
            cleaned.pop()
            if len(cleaned) > 1 and cleaned[-1] in CREDENTIAL_TOKENS:
                cleaned.pop()
            continue

        break

    return cleaned


def normalize_name_for_email(name: str) -> Tuple[str, str, str]:
    original = (name or "").strip()
    cleaned = re.sub(r"[,;]+$", "", original)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = strip_parenthetical_notes(cleaned)
    cleaned = strip_accents(cleaned)

    # Preserve hyphens inside tokens, remove apostrophes but keep letters.
    cleaned = cleaned.replace("'", "").replace("’", "")

    raw_parts = [p for p in cleaned.split(" ") if p]
    if len(raw_parts) < 2:
        raise ValueError(f"Could not derive firstname.lastname from name: {original}")

    parts = []
    for raw in raw_parts:
        token = clean_token(raw)
        if token:
            parts.append(token)

    parts = strip_trailing_credentials(parts)

    if len(parts) < 2:
        raise ValueError(f"Could not derive firstname.lastname from name: {original}")

    # Drop suffixes from the end
    while len(parts) > 1 and parts[-1] in SUFFIXES:
        parts.pop()

    if len(parts) < 2:
        raise ValueError(f"Could not derive firstname.lastname from name: {original}")

    first = parts[0]
    surname_parts = parts[1:]

    # Ignore single-letter middle initials when there is still a surname remaining
    while len(surname_parts) >= 2 and len(surname_parts[0]) == 1:
        surname_parts = surname_parts[1:]

    if not surname_parts:
        raise ValueError(f"Could not derive lastname from name: {original}")

    # Join all surname parts after the first name
    last = "".join(surname_parts)

    return cleaned, first, last


def build_email(site_name: str, person_name: str, domain: str) -> str:
    if (site_name or "").strip().upper() == SKIP_SITE:
        return ""
    if is_non_person_row(person_name):
        return ""
    _, first, last = normalize_name_for_email(person_name)
    return f"{first}.{last}@{domain}".lower()


def process_csv(input_path: Path, output_path: Path, domain: str) -> None:
    with open(input_path, "r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))

    if not rows:
        raise SystemExit("Input CSV is empty")

    header = rows[0]
    data = rows[1:]

    out_rows: List[List[str]] = []
    new_header = list(header[:2]) + ["generated_email"] + list(header[2:])
    out_rows.append(new_header)

    for row in data:
        row = list(row)
        while len(row) < 2:
            row.append("")
        site_name = row[0].strip()
        person_name = row[1].strip()
        generated_email = build_email(site_name, person_name, domain) if person_name else ""
        new_row = row[:2] + [generated_email] + row[2:]
        out_rows.append(new_row)

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerows(out_rows)


def process_xlsx(input_path: Path, output_path: Path, sheet_name: str, domain: str) -> None:
    wb = openpyxl.load_workbook(input_path)
    ws = wb[sheet_name] if sheet_name else wb[wb.sheetnames[0]]

    max_row = ws.max_row
    if max_row < 1:
        raise SystemExit("Input workbook is empty")

    ws.insert_cols(3, amount=1)
    ws.cell(row=1, column=3).value = "generated_email"

    for row_idx in range(2, max_row + 1):
        site_name = ws.cell(row=row_idx, column=1).value
        person_name = ws.cell(row=row_idx, column=2).value
        site_name = "" if site_name is None else str(site_name).strip()
        person_name = "" if person_name is None else str(person_name).strip()
        generated_email = build_email(site_name, person_name, domain) if person_name else ""
        ws.cell(row=row_idx, column=3).value = generated_email

    wb.save(output_path)


def main() -> int:
    args = parse_args()
    input_path = Path(args.input_file).resolve()
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")

    output_path = Path(args.output).resolve() if args.output else default_output_path(input_path)

    suffix = input_path.suffix.lower()
    if suffix == ".csv":
        process_csv(input_path, output_path, args.domain)
    elif suffix in {".xlsx", ".xlsm"}:
        process_xlsx(input_path, output_path, args.sheet_name, args.domain)
    else:
        raise SystemExit(f"Unsupported input type: {input_path.suffix}")

    print(f"Wrote: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
