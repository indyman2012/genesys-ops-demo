# Register Monday 5:00 AM weekly spam/abandon report task (8 runs, then auto-retire)
$ErrorActionPreference = "Stop"

$ProjectDir = "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
$TaskName = "GenesysToolkit-WeeklySpamAbandon"
$BatPath = Join-Path $ProjectDir "run_weekly_spam_abandon.bat"

if (-not (Test-Path $BatPath)) {
    throw "Missing launcher: $BatPath"
}
$EmailEnv = Join-Path $ProjectDir "email.env"
$DougEnv = Join-Path $ProjectDir "doug.env"
if (-not (Test-Path $EmailEnv) -and -not (Test-Path $DougEnv)) {
    Write-Warning "Neither email.env nor doug.env found in $ProjectDir - email step will fail until one exists."
}

$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "6:00AM"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Description = "Genesys weekly spam/short-abandon by queue (prior Mon-Sun). Auto-removes after 8 runs."

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description $Description | Out-Null

$LogsDir = Join-Path $ProjectDir "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir | Out-Null
}
$StateFile = Join-Path $LogsDir "weekly_spam_abandon_schedule.json"
$State = [ordered]@{
    task_name = $TaskName
    max_runs = 8
    runs_completed = 0
    registered_at = (Get-Date).ToUniversalTime().ToString("o")
}
$State | ConvertTo-Json | Set-Content -Path $StateFile -Encoding UTF8

Write-Host "[OK] Registered scheduled task: $TaskName"
Write-Host "     Every Monday at 6:00 AM (direct-outbound runs at 5:00 AM)"
Write-Host "     Auto-retire: deletes task after 8 successful weekly runs"
Write-Host "     Launcher: $BatPath"
Write-Host "     State:    $StateFile"
