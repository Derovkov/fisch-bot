@echo off
rem Opens the Fisch bot window without a terminal (the "Fisch bot" shortcut that
rem Install.bat makes does the same without this brief flash). Run Install.bat once first.
cd /d "%~dp0"
call :findpy
if not defined PY (
  echo Python was not found. Run Install.bat first.
  pause
  exit /b 1
)
for /f "delims=" %%i in ('%PY% -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set "PYW=%%i"
if exist "%PYW%" (
  start "" "%PYW%" "%~dp0FischBot.pyw"
) else (
  %PY% fischui.py
)
exit /b 0

:findpy
set "PY="
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13" && exit /b 0
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3" && exit /b 0
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python" && exit /b 0
exit /b 0
