"""Plain-language help text shown in demo mode for each tool."""

TOOL_HELP: dict[str, str] = {
    "license_counts": (
        "Counts how many users hold each Genesys license type across your organization. "
        "Use this for capacity planning and true-up conversations. Read-only — no changes are made."
    ),
    "provisioning_snapshot": (
        "Exports a CSV of users with name, email, title, division, and status. "
        "Useful for onboarding reviews, audits, and comparing Genesys to HR records. Read-only."
    ),
    "queue_members_export": (
        "Lists every agent on a queue with their joined status. "
        "Use before queue cleanup, role changes, or verifying who is active on a patient queue."
    ),
    "queue_joined_true": (
        "Sets joined=true so a user receives calls from queues whose names start with your prefix. "
        "Dry run shows which queues would be updated without changing membership."
    ),
    "queue_joined_false": (
        "Sets joined=false so a user stops receiving calls from matching queues but stays on the roster. "
        "Dry run previews the queues that would be deactivated for that user."
    ),
    "queue_remove_user": (
        "Removes a user from queue memberships entirely. "
        "Filter by queue prefix, or leave blank to target all queues the user belongs to. Dry run lists planned removals."
    ),
    "queue_remove_all": (
        "Finds every queue a user belongs to and removes them from all of them. "
        "Dry run scans the org and reports memberships without deleting anything."
    ),
    "external_contacts": (
        "Creates Genesys external contacts for phone numbers in your CSV that do not already exist. "
        "Dry run reports which numbers would be created vs skipped as duplicates."
    ),
    "extensions_by_title": (
        "Assigns the next available extension from a pool to users whose job title appears in your CSV. "
        "Dry run shows proposed x#### assignments without updating user records."
    ),
    "assign_group_patient_queues": (
        "Adds a Genesys group to the highest bullseye ring on every queue whose name starts with Patient. "
        "Dry run shows which queues would be updated and which already have the group."
    ),
    "wrapup_codes": (
        "Creates wrap-up codes and attaches them to NCM-prefix queues. "
        "Dry run validates queue matches and shows the payload that would be sent."
    ),
    "create_new_center": (
        "Reads a center provisioning CSV and builds a preflight plan: locations, sites, queues, and contacts. "
        "Dry run writes planned payloads to the log — no sites or queues are created yet."
    ),
    "patch_location_elin": (
        "Updates emergency numbers on locations to use ELIN type instead of static routing. "
        "Dry run scans all locations and reports which would change vs already correct."
    ),
    "bulk_update_sites": (
        "Updates caller ID name, caller ID number, and edge auto-update schedule on sites from CSV. "
        "Dry run validates each site match and shows planned field changes."
    ),
    "create_sites_from_csv": (
        "Creates Genesys sites that are missing but defined in your locations CSV. "
        "Dry run lists sites that would be POSTed vs already present."
    ),
    "create_outbound_routes": (
        "Creates default outbound route records for sites in your CSV. "
        "Dry run shows which routes would be created per site."
    ),
    "update_outbound_routes": (
        "Enables and configures existing outbound routes with the correct trunk assignment. "
        "Dry run previews PUT payloads per site."
    ),
    "audit_sites_locations": (
        "Compares your keep-list CSV against Genesys sites and locations, flagging candidates for deletion "
        "with dependency counts (phones, routes). Read-only audit report."
    ),
    "reassign_phones_hq": (
        "Moves phones from sites marked for deletion to a target site (default Headquarters). "
        "Dry run shows which phones would move without reassigning them."
    ),
    "role_level_assignment": (
        "Adds or removes division access on a user's roles using email and division names. "
        "Dry run prints the bulkadd/bulkremove payloads that would be sent to Genesys."
    ),
    "cx_license_review": (
        "Analyzes one user's permissions and classifies CX1 vs CX2 style access. "
        "Read-only — outputs a JSON review file for role design decisions."
    ),
    "create_cx1_test_role": (
        "Builds a voice-only test role from a license review JSON, stripping CX2 permissions. "
        "Dry run writes a preview JSON without creating the role in Genesys."
    ),
    "add_generated_emails": (
        "Offline utility: adds a generated @domain email column from first/last name in a spreadsheet. "
        "No Genesys API calls — runs entirely on your uploaded file."
    ),
    "permission_audit": (
        "Exports users, roles, permissions, and role-to-division mappings. "
        "Optional single-user filter. Read-only compliance and access review."
    ),
    "callback_sla_audit": (
        "Pulls Architect callback conversations for a date range and classifies SLA pass/fail/late. "
        "Read-only analytics — produces a CSV audit report."
    ),
    "validate_user_site_data": (
        "Cross-checks a reviewed user/site spreadsheet against live Genesys users, sites, and phones. "
        "Read-only — outputs ready vs manual-review CSVs."
    ),
    "cleanup_attachment_audit": (
        "Finds Genesys sites and locations not on your authoritative keep-list and summarizes attached phones. "
        "Read-only Excel workbook for cleanup planning."
    ),
}


def help_for_tool(tool_id: str) -> str:
    return TOOL_HELP.get(tool_id, "")


def enrich_tool(tool: dict) -> dict:
    enriched = dict(tool)
    enriched["help"] = help_for_tool(tool["id"])
    return enriched
