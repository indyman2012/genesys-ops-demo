#!/usr/bin/env python3
# Genesys Role Division Assignment Tool
# Version: 5.0.0 (FINAL - SUBJECT PATCH)

import argparse
import csv
import os
import requests
from dotenv import load_dotenv
import PureCloudPlatformClientV2

# -------- AUTH --------
load_dotenv("client.env")

CLIENT_ID = os.getenv("GENESYS_CLIENT_ID")
CLIENT_SECRET = os.getenv("GENESYS_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise Exception("Missing GENESYS_CLIENT_ID or GENESYS_CLIENT_SECRET")

region = PureCloudPlatformClientV2.PureCloudRegionHosts.us_west_2
BASE_URL = region.get_api_host()

api_client = PureCloudPlatformClientV2.api_client.ApiClient()
api_client.host = BASE_URL

token = api_client.get_client_credentials_token(CLIENT_ID, CLIENT_SECRET)
ACCESS_TOKEN = token.access_token

users_api = PureCloudPlatformClientV2.UsersApi(api_client)

HEADERS = {
    "Authorization": f"Bearer {ACCESS_TOKEN}",
    "Content-Type": "application/json"
}

# -------- HELPERS --------

def get_user(email):
    body = {
        "query": [{
            "type": "TERM",
            "fields": ["email"],
            "value": email
        }]
    }
    res = users_api.post_users_search(body)
    return res.results[0] if res.results else None

def get_division_id(name):
    url = f"{BASE_URL}/api/v2/authorization/divisions"
    r = requests.get(url, headers=HEADERS).json()
    for d in r.get("entities", []):
        if d["name"] == name:
            return d["id"]
    return None

def get_grants(user_id):
    url = f"{BASE_URL}/api/v2/authorization/subjects/{user_id}"
    r = requests.get(url, headers=HEADERS).json()
    return r.get("grants", [])

# -------- CORE --------

def update_roles(user_id, grants, division_id):
    new_grants = []

    for g in grants:
        new_grants.append({
            "roleId": g["role"]["id"],
            "divisionId": division_id
        })

    payload = {"grants": new_grants}

    url = f"{BASE_URL}/api/v2/authorization/subjects/{user_id}"
    r = requests.patch(url, headers=HEADERS, json=payload)

    print("Status:", r.status_code)
    print(r.text)

def process(email, division):
    print(f"\nProcessing {email}")

    user = get_user(email)
    if not user:
        print("User not found")
        return

    division_id = get_division_id(division)
    if not division_id:
        print("Division not found")
        return

    grants = get_grants(user.id)
    if not grants:
        print("No roles found")
        return

    print(f"User has {len(grants)} roles")

    update_roles(user.id, grants, division_id)

# -------- MAIN --------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--user")
    parser.add_argument("--division")
    parser.add_argument("--csv")

    args = parser.parse_args()

    if args.user and args.division:
        process(args.user, args.division)
        return

    if args.csv:
        with open(args.csv) as f:
            reader = csv.DictReader(f, delimiter="|")
            for row in reader:
                process(row["user_email"], row["division"])
        return

    print("Provide --user/--division or --csv")

if __name__ == "__main__":
    main()
