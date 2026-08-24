# Register Monday 6:30 AM weekly Queue Performance PDF export (Windows Task Scheduler)
$ErrorActionPreference = "Stop"

$ProjectDir = "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
$TaskName = "GenesysToolkit-WeeklyQueuePerformancePdf"
$BatPath = Join-Path $ProjectDir "run_weekly_queue_performance_pdf.bat"

# Set this to your team shared folder (UNC path or mapped drive), e.g.:
#   \\fileserver\ContactCenter\QueuePerformance
# Or leave empty to use outputs\queue_performance_exports under the project.
$SharedFolder = ""

if (-not (Test-Path $BatPath)) {
    throw "Missing launcher: $BatPath"
}

$ActionArgs = ""
if ($SharedFolder) {
    $ActionArgs = "--shared-folder `"$SharedFolder`""
}

$Action = New-ScheduledTaskAction -Execute $BatPath -Argument $ActionArgs -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "6:30AM"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Description = "Genesys Queue Performance PDF for prior Mon-Sun -> shared folder."

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description $Description | Out-Null

$LogsDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir | Out-Null
}
$StateFile = Join-Path $LogsDir "weekly_queue_performance_pdf_schedule.json"
$State = [ordered]@{
    task_name = $TaskName
    shared_folder = $SharedFolder
    registered_at = (Get-Date).ToUniversalTime().ToString("o")
}
$State | ConvertTo-Json | Set-Content -Path $StateFile -Encoding UTF8

Write-Host "[OK] Registered scheduled task: $TaskName"
Write-Host "     Every Monday at 6:30 AM (prior Mon-Sun window)"
Write-Host "     Launcher: $BatPath"
if ($SharedFolder) {
    Write-Host "     Shared folder: $SharedFolder"
} else {
    Write-Host "     Shared folder: outputs\queue_performance_exports (set `$SharedFolder in this script or QUEUE_PERFORMANCE_EXPORT_DIR env)"
}
Write-Host ""
Write-Host "Test now:"
Write-Host '  cd "' + $ProjectDir + '"'
Write-Host '  python jobs\weekly_queue_performance_pdf.py --dry-run'
