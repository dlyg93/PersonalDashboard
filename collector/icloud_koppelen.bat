@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m pip install --quiet --upgrade truststore icalendar recurring-ical-events
python icloud_koppelen.py
echo.
pause
