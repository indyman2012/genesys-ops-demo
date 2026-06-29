# One-time setup: create folders, migrate canonical files, archive superseded scripts.
$ErrorActionPreference = "Stop"
$Root = "C:\Users\douglash\Desktop\Python Files"
$Toolkit = Join-Path $Root "GenesysCloudToolkit"
$Beta = Join-Path $Root "BetaVersion"
$OldBeta = Join-Path $Root "OLD_BETA"
$Archive = Join-Path $Toolkit "_archive\superseded_scripts"

$dirs = @(
    "$Toolkit\app\routes", "$Toolkit\app\ui",
    "$Toolkit\core",
    "$Toolkit\dashboards\agent", "$Toolkit\dashboards\queue",
    "$Toolkit\dashboards\market", "$Toolkit\dashboards\org",
    "$Toolkit\tools\telephony", "$Toolkit\tools\users_roles",
    "$Toolkit\tools\queues", "$Toolkit\tools\audits", "$Toolkit\tools\reporting",
    "$Toolkit\jobs", "$Toolkit\schema",
    "$Toolkit\templates\components",
    "$Toolkit\static\css", "$Toolkit\static\js",
    "$Toolkit\templates_csv", "$Toolkit\logs", "$Toolkit\outputs",
    $Archive
)
foreach ($d in $dirs) { New-Item -ItemType Directory -Force -Path $d | Out-Null }

function Copy-IfExists($src, $dst) {
    if (Test-Path $src) {
        $parent = Split-Path $dst -Parent
        if (-not (Test-Path $parent)) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Copy-Item -Path $src -Destination $dst -Force
    }
}

# --- Core from BetaVersion + OLD_BETA ---
Copy-IfExists "$Beta\db.py" "$Toolkit\core\db.py"
Copy-IfExists "$OldBeta\genesys_client.py" "$Toolkit\core\genesys_client.py"
Copy-IfExists "$Beta\tools\genesys_api_cache_helpers.py" "$Toolkit\core\genesys_api_cache_helpers.py"
Copy-IfExists "$Beta\tools\genesys_pydantic_models.py" "$Toolkit\core\genesys_pydantic_models.py"
Copy-IfExists "$Beta\tools\pydantic_validation_helpers.py" "$Toolkit\core\pydantic_validation_helpers.py"
Copy-IfExists "$OldBeta\schema.sql" "$Toolkit\schema\schema.sql"
Copy-IfExists "$OldBeta\init_db.py" "$Toolkit\schema\init_db.py"
Copy-IfExists "$OldBeta\docker-compose.mysql.yml" "$Toolkit\docker-compose.mysql.yml"
Copy-IfExists "$OldBeta\tool_registry.py" "$Toolkit\tool_registry.py"
Copy-IfExists "$OldBeta\validate_setup.py" "$Toolkit\validate_setup.py"

# --- Dashboard apps (BetaVersion is canonical) ---
Copy-IfExists "$Beta\agent_perspective_metrics_app.py" "$Toolkit\dashboards\agent\app.py"
Copy-IfExists "$Beta\queue_perspective_metrics_app.py" "$Toolkit\dashboards\queue\app.py"
Copy-IfExists "$Beta\app\ui\agent_section_descriptions.py" "$Toolkit\app\ui\agent_section_descriptions.py"
Copy-IfExists "$Beta\app\ui\section_descriptions.py" "$Toolkit\app\ui\section_descriptions.py"
Copy-IfExists "$Beta\app\ui\metric_thresholds.py" "$Toolkit\app\ui\metric_thresholds.py"
Copy-IfExists "$Beta\templates\agent_perspective_metrics.html" "$Toolkit\templates\agent_perspective_metrics.html"
Copy-IfExists "$Beta\templates\queue_perspective_metrics.html" "$Toolkit\templates\queue_perspective_metrics.html"
Copy-IfExists "$Beta\templates\queue_dashboard.html" "$Toolkit\templates\queue_dashboard.html"
Copy-IfExists "$Beta\templates\components\section_help.html" "$Toolkit\templates\components\section_help.html"
Copy-IfExists "$Beta\static\css\agent_perspective_metrics.css" "$Toolkit\static\css\agent_perspective_metrics.css"
Copy-IfExists "$Beta\static\css\queue_perspective_metrics.css" "$Toolkit\static\css\queue_perspective_metrics.css"
Copy-IfExists "$Beta\static\js\agent_perspective_metrics.js" "$Toolkit\static\js\agent_perspective_metrics.js"
Copy-IfExists "$Beta\static\js\queue_perspective_metrics.js" "$Toolkit\static\js\queue_perspective_metrics.js"

# --- Jobs / reporting tools ---
Copy-IfExists "$Beta\tools\build_queue_daily_metrics.py" "$Toolkit\jobs\build_queue_daily_metrics.py"
Copy-IfExists "$Beta\tools\build_queue_daily_metrics_sqlalchemy.py" "$Toolkit\jobs\build_queue_daily_metrics_sqlalchemy.py"
Copy-IfExists "$Beta\tools\build_agent_daily_metrics_sqlalchemy.py" "$Toolkit\jobs\build_agent_daily_metrics_sqlalchemy.py"
Copy-IfExists "$Beta\tools\backfill_queue_daily_metrics_divisions.py" "$Toolkit\jobs\backfill_queue_daily_metrics_divisions.py"
Copy-IfExists "$Beta\tools\cleanup_mysql_retention.py" "$Toolkit\jobs\cleanup_mysql_retention.py"
Copy-IfExists "$Beta\tools\sync_genesys_users_cache.py" "$Toolkit\jobs\sync_genesys_users_cache.py"
Copy-IfExists "$Beta\tools\sync_genesys_queues_cache.py" "$Toolkit\jobs\sync_genesys_queues_cache.py"
Copy-IfExists "$OldBeta\tools\collect_reporting_data.py" "$Toolkit\jobs\collect_reporting_data.py"
Copy-IfExists "$OldBeta\tools\run_local_reports.py" "$Toolkit\tools\reporting\run_local_reports.py"

# --- OLD_BETA admin tools ---
$adminTools = @(
    "create_new_center.py", "extensions_by_title.py", "external_contacts.py",
    "generic_tool.py", "provisioning_snapshot.py",
    "queue_joined_false.py", "queue_joined_true.py",
    "queue_members_export.py", "queue_remove_user.py"
)
foreach ($t in $adminTools) {
    Copy-IfExists "$OldBeta\tools\$t" "$Toolkit\tools\queues\$t"
}
Copy-IfExists "$Beta\tools\remove_user_from_all_queues.py" "$Toolkit\tools\queues\remove_user_from_all_queues.py"
if (Test-Path "$OldBeta\templates_csv") {
    Copy-Item "$OldBeta\templates_csv\*" "$Toolkit\templates_csv\" -Force
}

# --- Root scripts: latest versions into categorized tools ---
$scriptMap = @{
    "genesys_add_generated_emails_v4.py" = "tools\users_roles\add_generated_emails.py"
    "genesys_role_assign_v6_7.py" = "tools\users_roles\role_assign.py"
    "genesys_role_division_tool_v5_final.py" = "tools\users_roles\role_division_tool.py"
    "genesys_user_cx_license_review.py" = "tools\users_roles\cx_license_review.py"
    "genesys_create_cx1_test_role.py" = "tools\users_roles\create_cx1_test_role.py"
    "genesys_license_counts.py" = "tools\users_roles\license_counts.py"
    "genesys_assign_group_to_patient_queue_highest_ring.py" = "tools\queues\assign_group_to_patient_queue_highest_ring.py"
    "genesys_wrapupcodes_apply_v2.py" = "tools\queues\wrapupcodes_apply.py"
    "genesys_audit_sites_locations_pre_delete_v3.py" = "tools\telephony\audit_sites_locations_pre_delete.py"
    "genesys_delete_sites_locations_full_pass_v2.py" = "tools\telephony\delete_sites_locations_full_pass.py"
    "genesys_reassign_phones_to_headquarters.py" = "tools\telephony\reassign_phones_to_headquarters.py"
    "genesys_bulk_update_sites.py" = "tools\telephony\bulk_update_sites.py"
    "genesys_create_sites_from_csv.py" = "tools\telephony\create_sites_from_csv.py"
    "genesys_create_outbound_routes.py" = "tools\telephony\create_outbound_routes.py"
    "genesys_update_outbound_routes.py" = "tools\telephony\update_outbound_routes.py"
    "genesys_patch_location_elin_full_emergency.py" = "tools\telephony\patch_location_elin.py"
    "genesys_callback_sla_audit_callback_plus_call_v2.py" = "tools\audits\callback_sla_audit.py"
    "genesys_cleanup_attachment_audit_v2.py" = "tools\audits\cleanup_attachment_audit.py"
    "genesys_permission_audit_clean_final.py" = "tools\audits\permission_audit.py"
    "genesys_validate_reviewed_user_site_data.py" = "tools\audits\validate_reviewed_user_site_data.py"
}
foreach ($entry in $scriptMap.GetEnumerator()) {
    Copy-IfExists (Join-Path $Root $entry.Key) (Join-Path $Toolkit $entry.Value)
}

# --- client.env (BetaVersion canonical, do not overwrite if exists) ---
if (-not (Test-Path "$Toolkit\client.env")) {
    if (Test-Path "$Beta\client.env") {
        Copy-Item "$Beta\client.env" "$Toolkit\client.env"
    } elseif (Test-Path "$OldBeta\client.env") {
        Copy-Item "$OldBeta\client.env" "$Toolkit\client.env"
    }
}
Copy-IfExists "$OldBeta\client.env.example" "$Toolkit\client.env.example"

# --- Archive all root-level genesys_*.py (canonical copies live under GenesysCloudToolkit) ---
$allGenesys = Get-ChildItem -Path $Root -Filter "genesys*.py" -File
$moved = 0
foreach ($f in $allGenesys) {
    Move-Item -Path $f.FullName -Destination (Join-Path $Archive $f.Name) -Force
    $moved++
}
Write-Host "Archived $moved superseded root scripts to _archive/superseded_scripts"
Write-Host "Setup complete: $Toolkit"
Write-Host "Note: BetaVersion/ and OLD_BETA/ left in place for reference until you confirm removal."
