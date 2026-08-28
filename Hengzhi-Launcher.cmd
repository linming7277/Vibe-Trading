@echo off
setlocal EnableExtensions

rem Double-click entry point for the Hengzhi local workbench.
rem This file intentionally contains ASCII only so cmd.exe works on every
rem Windows code page. User-facing Chinese text lives in the PowerShell UI.

set "ROOT=%~dp0"
set "LAUNCHER=%ROOT%launcher\windows\HengzhiLauncher.ps1"
set "WORKBENCH_URL="

if not exist "%LAUNCHER%" (
  echo [ERROR] Launcher script was not found.
  echo Expected: "%LAUNCHER%"
  pause
  exit /b 1
)

rem Default: a plain console monitor window (black screen).  It shows live
rem service states and accepts S/T/R/O/L/Q keys.  Closing the window never
rem stops the services; they run as independent hidden processes.
if /I "%~1"=="gui" (
  start "Hengzhi Launcher" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%LAUNCHER%" -Action gui
  exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -Action console
exit /b 0
