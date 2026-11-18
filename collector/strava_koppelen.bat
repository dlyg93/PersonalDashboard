@echo off
chcp 65001 >nul
cd /d "%~dp0"
python strava_koppelen.py
echo.
pause
