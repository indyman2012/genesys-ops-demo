@echo off
setlocal
cd /d "%~dp0"
set PYTHON_EXE=python
set LOG_DIR=logs
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set TS=%%i
set LOG_FILE=%LOG_DIR%\weekly_repeat_caller_launcher_%TS%.log
echo Starting weekly repeat caller job >> "%LOG_FILE%"
%PYTHON_EXE% jobs\weekly_repeat_caller_report.py >> "%LOG_FILE%" 2>&1
exit /b %ERRORLEVEL%
