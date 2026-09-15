# Register daily 6:00 AM Care Navigator multi-queue report (10 runs, then auto-retire)
$ErrorActionPreference = "Stop"

$ProjectDir = "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
$TaskName = "GenesysToolkit-DailyCareNavigatorMultiQueue"
$BatPath = Join-Path $ProjectDir "run_daily_care_navigator_multi_queue.bat"
$MaxRuns = 10

if (-not (Test-Path $BatPath)) {
    throw "Missing launcher: $BatPath"
}
$EmailEnv = Join-Path $ProjectDir "email.env"
$DougEnv = Join-Path $ProjectDir "doug.env"
if (-not (Test-Path $EmailEnv) -and -not (Test-Path $DougEnv)) {
    Write-Warning "Neither email.env nor doug.env found in $ProjectDir - email step will fail until one exists."
}

$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Daily -At "6:00AM"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Description = "Daily Care Navigator multi-queue join report (joined=true on 2+ queues). Auto-removes after $MaxRuns runs."

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description $Description | Out-Null

$LogsDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir | Out-Null
}
$StateFile = Join-Path $LogsDir "daily_care_navigator_multi_queue_schedule.json"
$State = [ordered]@{
    task_name = $TaskName
    max_runs = $MaxRuns
    runs_completed = 0
    registered_at = (Get-Date).ToUniversalTime().ToString("o")
}
$State | ConvertTo-Json | Set-Content -Path $StateFile -Encoding UTF8

Write-Host "[OK] Registered scheduled task: $TaskName"
Write-Host "     Every day at 6:00 AM (runs when available if PC was asleep)"
Write-Host "     Auto-retire: deletes task after $MaxRuns successful daily runs"
Write-Host "     Launcher: $BatPath"
Write-Host "     State:    $StateFile"
Write-Host ""
Write-Host "Test now:"
Write-Host "  cd `"$ProjectDir`""
Write-Host "  python jobs\daily_care_navigator_multi_queue_report.py --dry-run-email"
