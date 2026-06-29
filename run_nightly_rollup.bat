@echo off
setlocal enabledelayedexpansion

REM Genesys Cloud Toolkit - Nightly Rollup
set PROJECT_DIR=C:\Users\douglash\Desktop\Python Files\GenesysCloudToolkit
set PYTHON_EXE=python
set DAYS_BACK=2
set LOG_RETENTION_DAYS=90
set RAW_DB_RETENTION_DAYS=180
set ROLLUP_DB_RETENTION_DAYS=1095

set LOG_DIR=%PROJECT_DIR%\logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyy-MM-dd_HH-mm-ss"') do set TS=%%i
set LOG_FILE=%LOG_DIR%\rollup_%TS%.log

cd /d "%PROJECT_DIR%"

echo [1/5] queue_daily_metrics rollup >> "%LOG_FILE%"
%PYTHON_EXE% jobs\build_queue_daily_metrics.py --days-back %DAYS_BACK% --apply >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :END_FAIL

echo [2/5] agent_daily_metrics rollup >> "%LOG_FILE%"
%PYTHON_EXE% jobs\build_agent_daily_metrics_sqlalchemy.py --days-back %DAYS_BACK% --apply >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :END_FAIL

echo [3/5] division backfill >> "%LOG_FILE%"
%PYTHON_EXE% jobs\backfill_queue_daily_metrics_divisions.py --apply >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :END_FAIL

echo [4/5] MySQL retention >> "%LOG_FILE%"
%PYTHON_EXE% jobs\cleanup_mysql_retention.py --raw-days %RAW_DB_RETENTION_DAYS% --rollup-days %ROLLUP_DB_RETENTION_DAYS% --apply >> "%LOG_FILE%" 2>&1
if errorlevel 1 goto :END_FAIL

echo [5/5] log cleanup >> "%LOG_FILE%"
forfiles /p "%LOG_DIR%" /m rollup_*.log /d -%LOG_RETENTION_DAYS% /c "cmd /c del @path" >> "%LOG_FILE%" 2>&1

echo COMPLETED SUCCESSFULLY >> "%LOG_FILE%"
exit /b 0

:END_FAIL
echo FAILED >> "%LOG_FILE%"
exit /b 1
