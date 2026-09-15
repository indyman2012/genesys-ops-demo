$ProjectDir = "C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit"
$LogFile = Join-Path $ProjectDir "logs\business7d_outbound_3pm_wait.log"
$Target = (Get-Date).Date.AddHours(15)

function Log([string]$Message) {
    $Line = "[{0}] {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -Path $LogFile -Value $Line -Encoding UTF8
    Write-Host $Line
}

if ((Get-Date) -ge $Target) {
    Log "Already past 3:00 PM; running immediately."
} else {
    Log "Waiting until $Target ..."
    while ((Get-Date) -lt $Target) {
        Start-Sleep -Seconds 30
    }
}

Set-Location $ProjectDir
Log "Starting run_business7d_direct_outbound.bat"
& (Join-Path $ProjectDir "run_business7d_direct_outbound.bat")
$Code = $LASTEXITCODE
Log "Finished with exit code $Code"
exit $Code
