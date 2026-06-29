#!/usr/bin/env python3
# genesys_delete_sites_locations_full_pass_v2.py
# Adds retry handling for 429 / 5xx

import csv, os, base64, requests, time, random
from dotenv import load_dotenv

load_dotenv("client.env")

CLIENT_ID = os.getenv("GENESYS_CLIENT_ID")
CLIENT_SECRET = os.getenv("GENESYS_CLIENT_SECRET")
REGION = os.getenv("GENESYS_CLOUD_REGION", "usw2.pure.cloud")

TOKEN_URL = f"https://login.{REGION}/oauth/token"
API_BASE = f"https://api.{REGION}"

SITES_INPUT = "audit_sites_to_delete.csv"
LOCATIONS_INPUT = "audit_locations_to_delete.csv"


def log(level, msg):
    print(f"[{level}] {msg}")


def get_token():
    auth = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    r = requests.post(
        TOKEN_URL,
        headers={"Authorization": f"Basic {auth}"},
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def delete_with_retry(session, url, object_type, name, obj_id):
    for attempt in range(8):
        try:
            r = session.delete(url, timeout=60)

            if r.status_code in (200, 202, 204):
                log("SUCCESS", f"Deleted {object_type}: {name}")
                return "deleted", r.status_code, ""

            if r.status_code == 429 or r.status_code >= 500:
                delay = min(2 ** attempt, 30) + random.random()
                log("WARN", f"{object_type} {name} hit {r.status_code}, retry {attempt+1} in {delay:.2f}s")
                time.sleep(delay)
                continue

            log("ERROR", f"Failed ({r.status_code}) {object_type}: {name}")
            return "failed", r.status_code, r.text

        except Exception as e:
            delay = min(2 ** attempt, 30)
            log("WARN", f"Exception retry {attempt+1} for {object_type}: {name} -> {e}")
            time.sleep(delay)

    return "failed", "", "Max retries exceeded"


def process_file(session, input_file, object_type):
    results = []
    with open(input_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            obj_id = row["id"]
            name = row.get("site_name") or row.get("location_name")

            if object_type == "site":
                url = f"{API_BASE}/api/v2/telephony/providers/edges/sites/{obj_id}"
            else:
                url = f"{API_BASE}/api/v2/locations/{obj_id}"

            status, code, err = delete_with_retry(session, url, object_type, name, obj_id)

            results.append({
                "type": object_type,
                "name": name,
                "id": obj_id,
                "status": status,
                "http_status": code,
                "error": err
            })

    return results


def write_results(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["type","name","id","status","http_status","error"])
        writer.writeheader()
        writer.writerows(rows)


def main():
    token = get_token()
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {token}"})

    log("INFO", "Starting full delete pass with retry")

    site_results = process_file(session, SITES_INPUT, "site")
    write_results("delete_sites_results.csv", site_results)

    loc_results = process_file(session, LOCATIONS_INPUT, "location")
    write_results("delete_locations_results.csv", loc_results)

    log("SUCCESS", "Completed full delete pass")


if __name__ == "__main__":
    main()
