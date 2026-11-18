@echo off
chcp 65001 >nul
cd /d "%~dp0"
python collector.py --test
echo.
pause
