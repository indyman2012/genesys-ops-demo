# One-time 3:00 PM task: last-7-business-days direct outbound report + email
$ErrorActionPreference = "Stop"

$ProjectDir = "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
$TaskName = "GenesysToolkit-DirectOutbound-Business7d-1500"
$BatPath = Join-Path $ProjectDir "run_business7d_direct_outbound.bat"

if (-not (Test-Path $BatPath)) {
    throw "Missing launcher: $BatPath"
}

$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}

$Action = New-ScheduledTaskAction -Execute $BatPath -WorkingDirectory $ProjectDir
$AtThree = (Get-Date).Date.AddHours(15)
if ((Get-Date) -ge $AtThree) {
    throw "Already past 3:00 PM today; run run_business7d_direct_outbound.bat manually."
}
$Trigger = New-ScheduledTaskTrigger -Once -At $AtThree
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
$Description = "One-time 3 PM refresh: direct outbound agents, last 7 business days, email Doug."

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Description $Description | Out-Null

Write-Host "[OK] Registered one-time task: $TaskName"
Write-Host "     Today at 3:00 PM Eastern (StartWhenAvailable if PC was asleep)"
Write-Host "     Launcher: $BatPath"
