@echo off
setlocal
cd /d "%~dp0"
set PORT=8765
python site_picker_app.py
pause
