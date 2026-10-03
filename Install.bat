@echo off
rem Fisch bot setup - run once (again after updates). It:
rem   1. finds Python 3.10+ (offers to install Python 3.13 with winget if missing)
rem   2. installs the packages the bot needs
rem   3. makes "Fisch bot" shortcuts with the bot's icon (no terminal window)
setlocal
cd /d "%~dp0"
title Fisch bot setup
echo.
echo  Fisch bot setup
echo  ===============
echo.
call :findpy
if not defined PY (
  echo Python 3.10 or newer was not found.
  where winget >nul 2>&1
  if errorlevel 1 goto nopython
  choice /c YN /m "Install Python 3.13 now (winget, for this user only)"
  if errorlevel 2 goto nopython
  winget install -e --id Python.Python.3.13 --scope user --accept-package-agreements --accept-source-agreements
  call :findpy
  if not defined PY goto nopython
)
echo Using Python: %PY%
echo.
echo Installing packages (a minute or two the first time)...
%PY% -m pip install --user --upgrade --disable-pip-version-check -q pip
%PY% -m pip install --user --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
  echo.
  echo Installing packages failed - see the messages above.
  pause
  exit /b 1
)
for /f "delims=" %%i in ('%PY% -c "import sys, os; print(os.path.join(os.path.dirname(sys.executable), 'pythonw.exe'))"') do set "PYW=%%i"
if not exist "%PYW%" (
  echo Could not find pythonw.exe next to Python - use "Start Fisch bot.bat" instead.
  pause
  exit /b 1
)
echo.
echo Making shortcuts...
set "DESK=N"
choice /c YN /m "Also put a Fisch bot shortcut on your desktop"
if not errorlevel 2 set "DESK=Y"
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell; $dir = '%~dp0'.TrimEnd('\');" ^
  "$places = @($dir, [Environment]::GetFolderPath('Programs'));" ^
  "if ('%DESK%' -eq 'Y') { $places += [Environment]::GetFolderPath('Desktop') };" ^
  "foreach ($p in $places) { $s = $ws.CreateShortcut((Join-Path $p 'Fisch bot.lnk'));" ^
  "  $s.TargetPath = '%PYW%'; $s.Arguments = '\"' + (Join-Path $dir 'FischBot.pyw') + '\"';" ^
  "  $s.WorkingDirectory = $dir; $s.IconLocation = (Join-Path $dir 'ui\icons\app\fischbot.ico') + ',0';" ^
  "  $s.Description = 'Fisch bot'; $s.Save(); Write-Host ('  ' + $p) }"
echo.
echo Done. Open the bot with the "Fisch bot" shortcut (this folder, Start menu
echo or desktop) - it opens just the app, no terminal.
pause
exit /b 0

:nopython
echo.
echo Install Python 3.13 from https://www.python.org/downloads/
echo (tick "Add python.exe to PATH" during setup), then run Install.bat again.
pause
exit /b 1

:findpy
set "PY="
py -3.13 -c "import sys" >nul 2>&1 && set "PY=py -3.13" && exit /b 0
py -3 -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=py -3" && exit /b 0
python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1 && set "PY=python" && exit /b 0
rem just installed by winget: not on this window's PATH yet
if exist "%LOCALAPPDATA%\Programs\Python\Python313\python.exe" set PY="%LOCALAPPDATA%\Programs\Python\Python313\python.exe"&& exit /b 0
exit /b 0
