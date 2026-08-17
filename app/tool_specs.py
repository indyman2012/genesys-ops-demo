from __future__ import annotations

from typing import Any, Literal

ApplyFlag = Literal["apply", "execute", "dry_run_inverted", "none"]

FieldType = Literal[
    "file",
    "text",
    "email",
    "select",
    "date",
    "number",
    "checkbox",
    "textarea",
    "api_select",
]

FieldSpec = dict[str, Any]
ToolSpec = dict[str, Any]


def _f(
    name: str,
    field_type: FieldType,
    label: str,
    *,
    required: bool = False,
    default: str = "",
    choices: list[str] | None = None,
    api: str | None = None,
    arg: str | None = None,
    positional: bool = False,
    help_text: str = "",
) -> FieldSpec:
    return {
        "name": name,
        "type": field_type,
        "label": label,
        "required": required,
        "default": default,
        "choices": choices or [],
        "api": api,
        "arg": arg or (f"--{name.replace('_', '-')}" if not positional else name),
        "positional": positional,
        "help": help_text,
    }


TOOL_SPECS: list[ToolSpec] = [
    {
        "id": "license_counts",
        "name": "License Counts",
        "category": "Users / Roles",
        "script": "tools/users_roles/license_counts.py",
        "template": "",
        "description": "Aggregate license counts across the org (read-only).",
        "mutating": False,
        "apply_flag": "none",
        "fields": [],
    },
    {
        "id": "provisioning_snapshot",
        "name": "User Provisioning Snapshot",
        "category": "Audit",
        "script": "tools/queues/provisioning_snapshot.py",
        "template": "provisioning_snapshot_template.csv",
        "description": "Export user provisioning snapshot CSV.",
        "mutating": False,
        "apply_flag": "none",
        "fields": [
            _f("division", "api_select", "Division Filter", api="divisions", arg="--division"),
        ],
    },
    {
        "id": "queue_members_export",
        "name": "Export Queue Members",
        "category": "Queues",
        "script": "tools/queues/queue_members_export.py",
        "template": "queue_members_export_template.csv",
        "description": "Export queue members to CSV.",
        "mutating": False,
        "apply_flag": "none",
        "fields": [
            _f("csv_file", "file", "CSV Input", arg="csv_file", positional=True),
            _f("queue_name", "api_select", "Queue Name", api="queues", arg="--queue-name"),
        ],
    },
    {
        "id": "queue_joined_true",
        "name": "Queue Joined = True",
        "category": "Queues",
        "script": "tools/queues/queue_joined_true.py",
        "template": "queue_join_template.csv",
        "description": "Activate user on queues matching a prefix.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "CSV Input", arg="csv_file", positional=True),
            _f("user_email", "email", "User Email", arg="--user-email"),
            _f("queue_prefix", "text", "Queue Prefix", arg="--queue-prefix", help_text="Or pick a prefix pattern below."),
            _f("queue_prefix_select", "api_select", "Queue Prefix (from org)", api="queue_prefixes", arg="--queue-prefix"),
        ],
    },
    {
        "id": "queue_joined_false",
        "name": "Queue Joined = False",
        "category": "Queues",
        "script": "tools/queues/queue_joined_false.py",
        "template": "queue_join_template.csv",
        "description": "Deactivate user on queues matching a prefix.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "CSV Input", arg="csv_file", positional=True),
            _f("user_email", "email", "User Email", arg="--user-email"),
            _f("queue_prefix", "text", "Queue Prefix", arg="--queue-prefix"),
            _f("queue_prefix_select", "api_select", "Queue Prefix (from org)", api="queue_prefixes", arg="--queue-prefix"),
        ],
    },
    {
        "id": "queue_remove_user",
        "name": "Remove User from Queues",
        "category": "Queues",
        "script": "tools/queues/queue_remove_user.py",
        "template": "queue_join_template.csv",
        "description": "Remove user from queues by prefix (blank prefix = all user queues).",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "CSV Input", arg="csv_file", positional=True),
            _f("user_email", "email", "User Email", arg="--user-email"),
            _f("queue_prefix", "text", "Queue Prefix", arg="--queue-prefix"),
        ],
    },
    {
        "id": "queue_remove_all",
        "name": "Remove User from All Queues",
        "category": "Queues",
        "script": "tools/queues/remove_user_from_all_queues.py",
        "template": "",
        "description": "Remove user from every queue membership in the org.",
        "mutating": True,
        "apply_flag": "apply",
        "warning": "Removes the user from ALL queue memberships.",
        "fields": [
            _f("email", "email", "User Email", required=True, arg="--email"),
        ],
    },
    {
        "id": "external_contacts",
        "name": "External Contacts",
        "category": "Directory",
        "script": "tools/queues/external_contacts.py",
        "template": "external_contacts_template.csv",
        "description": "Create missing external contacts from CSV.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "CSV Input", required=True, arg="csv_file", positional=True),
        ],
    },
    {
        "id": "extensions_by_title",
        "name": "Extension Assignment by Title",
        "category": "Telephony",
        "script": "tools/queues/extensions_by_title.py",
        "template": "extension_titles_template.csv",
        "description": "Assign extensions from pool by user title.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "CSV Input", required=True, arg="csv_file", positional=True),
            _f("extension_pool", "select", "Extension Pool", default="8300-8500", choices=["8300-8500", "15100-15999"], arg="--extension-pool"),
        ],
    },
    {
        "id": "assign_group_patient_queues",
        "name": "Assign Group to Patient Queues",
        "category": "Queues",
        "script": "tools/queues/assign_group_to_patient_queue_highest_ring.py",
        "template": "",
        "description": "Add group to highest ring on Patient* queues.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("group", "api_select", "Group", required=True, api="groups", arg="--group"),
        ],
    },
    {
        "id": "wrapup_codes",
        "name": "Wrap-up Code Manager",
        "category": "Queues",
        "script": "tools/queues/wrapupcodes_apply.py",
        "template": "wrapup_codes_template.csv",
        "description": "Create/assign wrap-up codes to NCM-prefix queues.",
        "mutating": True,
        "apply_flag": "execute",
        "fields": [
            _f("input", "file", "Wrap-up Codes File", required=True, arg="--input"),
            _f("queue_prefix", "text", "Queue Prefix", default="NCM", arg="--queue-prefix"),
            _f("remove_all_existing", "checkbox", "Remove all existing wrap-up codes first", arg="--remove-all-existing"),
        ],
    },
    {
        "id": "create_new_center",
        "name": "Create New Center (Preflight)",
        "category": "Telephony",
        "script": "tools/queues/create_new_center.py",
        "template": "create_new_center_template.csv",
        "description": "Preflight planner for new center provisioning.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "Center CSV", required=True, arg="csv_file", positional=True),
        ],
    },
    {
        "id": "patch_location_elin",
        "name": "Patch Location ELIN",
        "category": "Telephony",
        "script": "tools/telephony/patch_location_elin.py",
        "template": "",
        "description": "Set emergency number type to ELIN on all locations.",
        "mutating": True,
        "apply_flag": "apply",
        "warning": "Patches every location with an emergency number.",
        "fields": [],
    },
    {
        "id": "bulk_update_sites",
        "name": "Bulk Update Sites",
        "category": "Telephony",
        "script": "tools/telephony/bulk_update_sites.py",
        "template": "",
        "description": "Bulk update site caller ID and auto-update settings.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "Sites CSV", required=True, arg="csv_file", positional=True),
        ],
    },
    {
        "id": "create_sites_from_csv",
        "name": "Create Sites from CSV",
        "category": "Telephony",
        "script": "tools/telephony/create_sites_from_csv.py",
        "template": "create_sites_locations_template.csv",
        "description": "Create missing sites linked to locations.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "Sites CSV", required=True, arg="csv_file", positional=True),
        ],
    },
    {
        "id": "create_outbound_routes",
        "name": "Create Outbound Routes",
        "category": "Telephony",
        "script": "tools/telephony/create_outbound_routes.py",
        "template": "",
        "description": "Create default outbound routes per site.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "Sites CSV", required=True, arg="csv_file", positional=True),
        ],
    },
    {
        "id": "update_outbound_routes",
        "name": "Update Outbound Routes",
        "category": "Telephony",
        "script": "tools/telephony/update_outbound_routes.py",
        "template": "",
        "description": "Enable and configure existing outbound routes.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv_file", "file", "Sites CSV", required=True, arg="csv_file", positional=True),
        ],
    },
    {
        "id": "audit_sites_locations",
        "name": "Audit Sites / Locations Pre-Delete",
        "category": "Telephony",
        "script": "tools/telephony/audit_sites_locations_pre_delete.py",
        "template": "",
        "description": "Pre-delete dependency audit (read-only report).",
        "mutating": False,
        "apply_flag": "none",
        "fields": [
            _f("csv", "file", "Keep-list CSV", required=True, arg="--csv"),
        ],
    },
    {
        "id": "reassign_phones_hq",
        "name": "Reassign Phones to Headquarters",
        "category": "Telephony",
        "script": "tools/telephony/reassign_phones_to_headquarters.py",
        "template": "",
        "description": "Move phones from delete-candidate sites to Headquarters.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("csv", "file", "Audit CSV", arg="--csv"),
            _f("target_site", "text", "Target Site", default="Headquarters", arg="--target-site"),
        ],
    },
    {
        "id": "delete_sites_locations",
        "name": "Delete Sites / Locations",
        "category": "Telephony",
        "script": "tools/telephony/delete_sites_locations_full_pass.py",
        "template": "",
        "description": "DELETE sites and locations from audit CSVs.",
        "mutating": True,
        "apply_flag": "none",
        "warning": "DESTRUCTIVE: Deletes sites and locations. Script may run immediately.",
        "fields": [],
    },
    {
        "id": "role_level_assignment",
        "name": "Role Division Assignment",
        "category": "Users / Roles",
        "script": "tools/users_roles/role_assign.py",
        "template": "role_level_assignment_template.csv",
        "description": "Add/remove division grants across user roles.",
        "mutating": True,
        "apply_flag": "dry_run_inverted",
        "fields": [
            _f("csv", "file", "Pipe-delimited CSV", arg="--csv"),
            _f("user", "email", "User Email", arg="--user"),
            _f("division", "text", "Division(s) to Add", arg="--division", help_text="Comma-separated or use CSV."),
            _f("remdiv", "text", "Division(s) to Remove", arg="--remdiv"),
        ],
    },
    {
        "id": "cx_license_review",
        "name": "CX License Review",
        "category": "Users / Roles",
        "script": "tools/users_roles/cx_license_review.py",
        "template": "",
        "description": "Review CX1 vs CX2 permissions for a user (read-only).",
        "mutating": False,
        "apply_flag": "none",
        "fields": [
            _f("email", "email", "User Email", required=True, arg="--email"),
        ],
    },
    {
        "id": "create_roaming_agent_role",
        "name": "Create Roaming Agent Role",
        "category": "Users / Roles",
        "script": "tools/users_roles/create_roaming_agent_role.py",
        "template": "",
        "description": "Create/update minimal Site Picker role (Roaming Agent).",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("site_ids", "text", "Site IDs (comma-separated)", arg="--site-ids"),
            _f("site_names", "text", "Site names (comma-separated)", arg="--site-names"),
            _f("all_sites", "checkbox", "All telephony sites", arg="--all-sites"),
        ],
    },
    {
        "id": "create_cx1_test_role",
        "name": "Create CX1 Test Role",
        "category": "Users / Roles",
        "script": "tools/users_roles/create_cx1_test_role.py",
        "template": "",
        "description": "Build CX1 test role from license review JSON.",
        "mutating": True,
        "apply_flag": "apply",
        "fields": [
            _f("review_json", "file", "Review JSON", arg="--review-json"),
            _f("role_name", "text", "Role Name", default="CX1 Test", arg="--role-name"),
        ],
    },
    {
        "id": "add_generated_emails",
        "name": "Add Generated Emails (Offline)",
        "category": "Users / Roles",
        "script": "tools/users_roles/add_generated_emails.py",
        "template": "",
        "description": "Generate email addresses from names in CSV/XLSX (no API calls).",
        "mutating": False,
        "apply_flag": "none",
        "fields": [
            _f("input_file", "file", "Input CSV/XLSX", required=True, arg="input_file", positional=True),
            _f("domain", "text", "Email Domain", default="archwellhealth.com", arg="--domain"),
        ],
    },
    {
        "id": "permission_audit",
        "name": "Permission Audit",
        "category": "Audits",
        "script": "tools/audits/permission_audit.py",
        "template": "",
        "description": "Export users, roles, permissions (read-only).",
        "mutating": False,
        "apply_flag": "none",
        "fields": [
            _f("userEmail", "email", "Filter by User Email", arg="--userEmail"),
        ],
    },
    {
        "id": "callback_sla_audit",
        "name": "Callback SLA Audit",
        "category": "Audits",
        "script": "tools/audits/callback_sla_audit.py",
        "template": "",
        "description": "Architect callback SLA audit report.",
        "mutating": False,
        "apply_flag": "none",
        "fields": [
            _f("start", "date", "Start Date", required=True, arg="--start"),
            _f("end", "date", "End Date", required=True, arg="--end"),
            _f("sla_minutes", "number", "SLA Minutes", default="10", arg="--sla-minutes"),
            _f("queue_id", "api_select", "Queue Filter", api="queue_ids", arg="--queue-id"),
        ],
    },
    {
        "id": "validate_user_site_data",
        "name": "Validate User Site Data",
        "category": "Audits",
        "script": "tools/audits/validate_reviewed_user_site_data.py",
        "template": "",
        "description": "Validate reviewed user/site CSV against Genesys.",
        "mutating": False,
        "apply_flag": "none",
        "fields": [
            _f("input_file", "file", "Reviewed CSV", required=True, arg="input_file", positional=True),
        ],
    },
    {
        "id": "cleanup_attachment_audit",
        "name": "Cleanup Attachment Audit",
        "category": "Audits",
        "script": "tools/audits/cleanup_attachment_audit.py",
        "template": "",
        "description": "Audit extra sites/locations vs keep-list CSV.",
        "mutating": False,
        "apply_flag": "none",
        "fields": [
            _f("csv", "file", "Keep-list CSV", required=True, arg="--csv"),
        ],
    },
]


def tools_by_category() -> dict[str, list[ToolSpec]]:
    grouped: dict[str, list[ToolSpec]] = {}
    for tool in TOOL_SPECS:
        grouped.setdefault(tool["category"], []).append(tool)
    for items in grouped.values():
        items.sort(key=lambda t: t["name"].lower())
    return dict(sorted(grouped.items(), key=lambda kv: kv[0]))


def get_tool(tool_id: str) -> ToolSpec | None:
    for tool in TOOL_SPECS:
        if tool["id"] == tool_id:
            return tool
    return None
