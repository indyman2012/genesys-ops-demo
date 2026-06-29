#!/usr/bin/env python3

"""
Genesys Permission Audit Script (Clean / Minimal)

Purpose:
Export users, roles, permissions, and user-role-division assignments
from Genesys Cloud using the official Python SDK.

Outputs:
    users.csv
    roles.csv
    permissions.csv
    user_role_division_map.csv

Notes:
- Handles SDK nested objects properly
- Uses safe pagination patterns
"""

import csv
import os
import argparse
from dotenv import load_dotenv
import PureCloudPlatformClientV2
from PureCloudPlatformClientV2.rest import ApiException

# -----------------------------
# Load credentials
# -----------------------------

load_dotenv("client.env")

CLIENT_ID = os.getenv("GENESYS_CLIENT_ID")
CLIENT_SECRET = os.getenv("GENESYS_CLIENT_SECRET")

if not CLIENT_ID or not CLIENT_SECRET:
    raise Exception("Missing GENESYS_CLIENT_ID or GENESYS_CLIENT_SECRET in client.env")

# -----------------------------
# Authenticate
# -----------------------------

PureCloudPlatformClientV2.configuration.host = (
    PureCloudPlatformClientV2.PureCloudRegionHosts.us_west_2.get_api_host()
)

api_client = (
    PureCloudPlatformClientV2.api_client.ApiClient()
    .get_client_credentials_token(CLIENT_ID, CLIENT_SECRET)
)

users_api = PureCloudPlatformClientV2.UsersApi(api_client)
auth_api = PureCloudPlatformClientV2.AuthorizationApi(api_client)

# -----------------------------
# API helper
# -----------------------------

def api_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except ApiException as e:
        print("API error:", e)
        raise

# -----------------------------
# Load users
# -----------------------------

def get_all_users():

    users = []
    page = 1

    while True:

        resp = api_call(users_api.get_users, page_number=page)

        users.extend(resp.entities)

        if page >= resp.page_count:
            break

        page += 1

    return users


def get_user_by_email(email):

    users = get_all_users()

    for user in users:
        if user.email and user.email.lower() == email.lower():
            return user

    return None


# -----------------------------
# Load divisions
# -----------------------------

def get_divisions():

    divisions = {}
    page = 1

    while True:

        resp = api_call(
            auth_api.get_authorization_divisions,
            page_number=page,
            page_size=100
        )

        for d in resp.entities:
            divisions[d.id] = d.name

        if page >= resp.page_count:
            break

        page += 1

    return divisions


# -----------------------------
# Load roles
# -----------------------------

def get_roles():

    roles = {}
    page = 1

    while True:

        resp = api_call(
            auth_api.get_authorization_roles,
            page_number=page,
            page_size=100
        )

        for r in resp.entities:
            roles[r.id] = r

        if page >= resp.page_count:
            break

        page += 1

    return roles


# -----------------------------
# Load grants
# -----------------------------

def get_user_grants(user_id):

    subject = api_call(auth_api.get_authorization_subject, user_id)

    return subject.grants


# -----------------------------
# Main
# -----------------------------

def main():

    parser = argparse.ArgumentParser()
    parser.add_argument("--userEmail", help="Optional single user audit")

    args = parser.parse_args()

    print("Loading divisions...")
    divisions = get_divisions()

    print("Loading roles...")
    roles = get_roles()

    print("Loading users...")

    if args.userEmail:

        user = get_user_by_email(args.userEmail)

        if not user:
            print("User not found")
            return

        users = [user]

    else:

        users = get_all_users()

    users_rows = []
    roles_rows = []
    permissions_rows = []
    mapping_rows = []

    # -----------------------------
    # Roles + permissions
    # -----------------------------

    for role_id, role in roles.items():

        roles_rows.append([
            role.id,
            role.name,
            role.description or ""
        ])

        if role.permissions:
            for perm in role.permissions:
                permissions_rows.append([
                    role.id,
                    role.name,
                    perm
                ])

    # -----------------------------
    # Process users
    # -----------------------------

    for user in users:

        print("Processing:", user.email)

        users_rows.append([
            user.id,
            user.email,
            user.name,
            user.state
        ])

        grants = get_user_grants(user.id)

        for grant in grants:

            role_id = grant.role.id
            role = roles.get(role_id)

            if not role:
                continue

            if grant.division:
                division_id = grant.division.id
            else:
                division_id = "ALL_DIVISIONS"

            division_name = divisions.get(division_id, "All Divisions")

            mapping_rows.append([
                user.id,
                user.email,
                role.id,
                role.name,
                division_id,
                division_name
            ])

    # -----------------------------
    # Write files
    # -----------------------------

    with open("users.csv","w",newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["user_id","email","name","state"])
        writer.writerows(users_rows)

    with open("roles.csv","w",newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["role_id","role_name","description"])
        writer.writerows(roles_rows)

    with open("permissions.csv","w",newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["role_id","role_name","permission"])
        writer.writerows(permissions_rows)

    with open("user_role_division_map.csv","w",newline="") as f:
        writer = csv.writer(f)
        writer.writerow([
            "user_id",
            "email",
            "role_id",
            "role_name",
            "division_id",
            "division_name"
        ])
        writer.writerows(mapping_rows)

    print("\nExport complete.")
    print("Generated: users.csv, roles.csv, permissions.csv, user_role_division_map.csv")


if __name__ == "__main__":
    main()
