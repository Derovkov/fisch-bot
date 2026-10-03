@echo off
rem Run this ONCE: installs the Python packages the Fisch bot needs.
cd /d "%~dp0"
call :findpy
if not defined PY (
  echo Python 3.10 or newer was not found.
  echo Install Python 3.13 from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup, then run this again.
  pause
  exit /b 1
)
echo Using: %PY%
%PY% -m pip install --user --upgrade pip
%PY% -m pip install --user -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installing failed - see the messages above.
  pause
  exit /b 1
)
echo.
echo Done. Double-click "Start Fisch bot.bat" to open the bot.
pause
exit /b 0

:findpy
set "PY="
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13" && exit /b 0
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3" && exit /b 0
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python" && exit /b 0
exit /b 0
