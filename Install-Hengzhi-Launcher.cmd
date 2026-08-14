@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0launcher\windows\HengzhiLauncher.ps1" -Action install-shortcut
if errorlevel 1 (
  echo Failed to install launcher shortcut.
  pause
)
endlocal
