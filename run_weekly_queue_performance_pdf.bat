@echo off
setlocal
cd /d "%~dp0"
python jobs\weekly_queue_performance_pdf.py %*
exit /b %ERRORLEVEL%
