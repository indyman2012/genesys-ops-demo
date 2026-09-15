# Register Monday 8:00 AM weekly repeat caller report task (8 runs, then auto-retire)
$ErrorActionPreference = "Stop"
$ProjectDir = "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
$TaskName = "GenesysToolkit-WeeklyRepeatCaller"
$BatPath = Join-Path $ProjectDir "run_weekly_repeat_caller.bat"
if (-not (Test-Path $BatPath)) { throw "Missing launcher: $BatPath" }
$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $ProjectDir
$Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday -At "8:00AM"
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Description = "Genesys weekly repeat inbound caller by queue (WoW). Auto-removes after 8 runs."
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false }
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description $Description | Out-Null
$StateFile = Join-Path (Join-Path $ProjectDir "logs") "weekly_repeat_caller_schedule.json"
@{ task_name = $TaskName; max_runs = 8; runs_completed = 0; registered_at = (Get-Date).ToUniversalTime().ToString("o") } | ConvertTo-Json | Set-Content -Path $StateFile -Encoding utf8
Write-Host "[OK] Registered scheduled task: $TaskName (Monday 8:00 AM, 8 runs)"
