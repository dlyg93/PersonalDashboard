@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo === Claude Personal Dashboard - installatie ===
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [FOUT] Python niet gevonden. Installeer Python van python.org en vink "Add to PATH" aan.
  pause
  exit /b 1
)

echo [1/3] Python-pakket MetaTrader5 installeren...
python -m pip install --upgrade MetaTrader5 truststore icalendar recurring-ical-events
echo.

if not exist config.json (
  copy config.example.json config.json >nul
  echo [2/3] config.json aangemaakt. Pas de MT5-paden aan en start dit bestand opnieuw.
  notepad config.json
  pause
  exit /b 0
)
echo [2/3] config.json bestaat al.
echo.

echo [3/3] Taak "Claude Personal Dashboard" inplannen (elke minuut; volledige meting om de 5 minuten)...
set "PYW="
for /f "delims=" %%i in ('python -c "import sys,os;print(os.path.join(os.path.dirname(sys.executable),'pythonw.exe'))"') do set "PYW=%%i"
schtasks /Create /F /SC MINUTE /MO 1 /TN "Claude Personal Dashboard" /TR "\"%PYW%\" \"%~dp0collector.py\""
echo.

echo Eerste meting draaien...
python collector.py --now
echo.
echo Klaar. Het script draait nu op de achtergrond: elke minuut kijkt het naar opdrachten, om de 5 minuten haalt het nieuwe cijfers.
echo Log: collector.log in deze map.
pause
