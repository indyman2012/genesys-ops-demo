# Register Monday 9:00 AM biweekly AHT trend report task (runs every other week via job state)
$ErrorActionPreference = "Stop"

$ProjectDir = "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
$TaskName = "GenesysToolkit-BiweeklyAhtTrend"
$BatPath = Join-Path $ProjectDir "run_biweekly_aht_trend.bat"

if (-not (Test-Path $BatPath)) {
    throw "Missing launcher: $BatPath"
}

$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "9:00AM"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Description = "Genesys biweekly inbound voice AHT HTML (4 weeks, handle components + daily ASA, queue dropdown). Job skips if run within 13 days."

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description $Description | Out-Null

$LogsDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir | Out-Null
}
$StateFile = Join-Path $LogsDir "biweekly_aht_trend_schedule.json"
if (-not (Test-Path $StateFile)) {
    $State = [ordered]@{
        task_name = $TaskName
        min_days_between_runs = 13
        runs_completed = 0
        registered_at = (Get-Date).ToUniversalTime().ToString("o")
    }
    $State | ConvertTo-Json | Set-Content -Path $StateFile -Encoding utf8
}

Write-Host "[OK] Registered scheduled task: $TaskName"
Write-Host "     Every Monday at 9:00 AM (job runs biweekly via 13-day gate)"
Write-Host "     Launcher: $BatPath"
Write-Host "     State:    $StateFile"
