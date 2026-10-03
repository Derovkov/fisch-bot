@echo off
rem Double-click to open the Fisch bot window. Run Install.bat once first.
cd /d "%~dp0"
call :findpy
if not defined PY (
  echo Python was not found. Install Python 3.13 from https://www.python.org/downloads/
  echo ^(tick "Add python.exe to PATH"^), then run Install.bat.
  pause
  exit /b 1
)
%PY% fischui.py
if errorlevel 1 (
  echo.
  echo The Fisch bot closed with an error - see the message above.
  echo If it says a module is missing, run Install.bat first.
  pause
)
exit /b 0

:findpy
set "PY="
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13" && exit /b 0
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3" && exit /b 0
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python" && exit /b 0
exit /b 0
