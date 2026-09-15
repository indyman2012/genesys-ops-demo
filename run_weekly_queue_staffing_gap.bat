@echo off
setlocal enabledelayedexpansion

REM Genesys Cloud Toolkit - Weekly Queue Staffing Gap Report (Mon 7 AM task)
set PROJECT_DIR=C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit
set PYTHON_EXE=python
set LOG_DIR=%PROJECT_DIR%\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set TS=%%i
set LOG_FILE=%LOG_DIR%\weekly_queue_staffing_gap_launcher_%TS%.log

cd /d "%PROJECT_DIR%"
echo Starting weekly queue staffing gap job >> "%LOG_FILE%"
%PYTHON_EXE% jobs\weekly_queue_staffing_gap_report.py >> "%LOG_FILE%" 2>&1
if errorlevel 1 (
  echo FAILED >> "%LOG_FILE%"
  exit /b 1
)
echo COMPLETED SUCCESSFULLY >> "%LOG_FILE%"
exit /b 0
