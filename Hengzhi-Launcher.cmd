@echo off
setlocal EnableExtensions

rem Double-click entry point for the Hengzhi local workbench.
rem This file intentionally contains ASCII only so cmd.exe works on every
rem Windows code page. User-facing Chinese text lives in the PowerShell UI.

set "ROOT=%~dp0"
set "LAUNCHER=%ROOT%launcher\windows\HengzhiLauncher.ps1"
set "WORKBENCH_URL=http://127.0.0.1:5899/today"

if not exist "%LAUNCHER%" (
  echo [ERROR] Launcher script was not found.
  echo Expected: "%LAUNCHER%"
  pause
  exit /b 1
)

if /I "%~1"=="gui" (
  start "Hengzhi Launcher" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%LAUNCHER%" -Action gui
  exit /b 0
)

if /I "%~1"=="status" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -Action status
  exit /b %ERRORLEVEL%
)

echo Restarting backend and frontend. Please wait...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -Action restart
if errorlevel 1 goto :failed

echo Opening workbench...
start "" "%WORKBENCH_URL%"
exit /b 0

:failed
echo [ERROR] The local stack did not start. Check .launcher\logs for details.
pause
exit /b 1
