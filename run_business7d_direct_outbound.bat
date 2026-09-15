@echo off
setlocal enabledelayedexpansion

set PROJECT_DIR=C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit
set PYTHON_EXE=C:\Users\douglash\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe
set LOG_DIR=%PROJECT_DIR%\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set STAMP=%%i
set OUT=%PROJECT_DIR%\outputs\direct_outbound_agents_business7d_%STAMP%.xlsx
set LOG_FILE=%LOG_DIR%\business7d_direct_outbound_%STAMP%.log

cd /d "%PROJECT_DIR%"
echo Starting business7d direct outbound job >> "%LOG_FILE%"
echo OUT=%OUT% >> "%LOG_FILE%"

"%PYTHON_EXE%" tools\reporting\direct_outbound_agents_report.py --period business-days --days 7 --adaptive-threshold --output "%OUT%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo REPORT FAILED >> "%LOG_FILE%"
  exit /b 1
)
if not exist "%OUT%" (
  echo REPORT FILE MISSING: %OUT% >> "%LOG_FILE%"
  exit /b 1
)

set BODY_FILE=%LOG_DIR%\business7d_outbound_email_body.txt
(
echo Hi Doug,
echo.
echo Attached is the direct-outbound agents report for the last 7 business days ^(Mon-Fri only; see About sheet for exact dates^).
echo.
echo Purpose: educational coaching for direct dials that should have been placed on behalf of a queue.
echo.
echo — GenesysCloudToolkit
) > "%BODY_FILE%"

"%PYTHON_EXE%" tools\email\gmail_send.py --to doug.hendrickson@archwellhealth.com --subject "Direct Outbound Agents Report — Last 7 Business Days (3 PM refresh)" --body-file "%BODY_FILE%" --attach "%OUT%" >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo EMAIL FAILED >> "%LOG_FILE%"
  exit /b 1
)

echo COMPLETED SUCCESSFULLY >> "%LOG_FILE%"
exit /b 0
