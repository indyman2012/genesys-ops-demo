# Register Monday 3:00 AM weekly queue staffing gap report task (ongoing)
$ErrorActionPreference = "Stop"

$ProjectDir = "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
$TaskName = "GenesysToolkit-WeeklyQueueStaffingGap"
$BatPath = Join-Path $ProjectDir "run_weekly_queue_staffing_gap.bat"

if (-not (Test-Path $BatPath)) {
    throw "Missing launcher: $BatPath"
}

$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "3:00AM"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Description = "Genesys weekly PS queue staffing gap (prior Mon-Sun). Runs every Monday 3:00 AM."

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description $Description | Out-Null

$LogsDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir | Out-Null
}
$StateFile = Join-Path $LogsDir "weekly_queue_staffing_gap_schedule.json"
$State = [ordered]@{
    task_name = $TaskName
    registered_at = (Get-Date).ToUniversalTime().ToString("o")
    schedule = "Monday 3:00 AM local"
}
$State | ConvertTo-Json | Set-Content -Path $StateFile -Encoding utf8

Write-Host "[OK] Registered scheduled task: $TaskName"
Write-Host "     Every Monday at 3:00 AM"
Write-Host "     Launcher: $BatPath"
Write-Host "     State:    $StateFile"
