@echo off
setlocal EnableExtensions

rem Double-click entry point for the Hengzhi local workbench.
rem ASCII-only wrapper; UI strings live in launcher/windows/strings.zh-CN.json

set "ROOT=%~dp0"
set "LAUNCHER=%ROOT%launcher\windows\HengzhiLauncher.ps1"

if not exist "%LAUNCHER%" (
  echo [ERROR] Launcher script was not found.
  echo Expected: "%LAUNCHER%"
  pause
  exit /b 1
)

rem Default: black console service monitor with auto-start.
if /I "%~1"=="gui" (
  powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -Action gui -AutoStart
  if errorlevel 1 (
    echo.
    echo [ERROR] Launcher failed. See the message above.
    pause
  )
  exit /b 0
)

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%LAUNCHER%" -Action console -AutoStart
if errorlevel 1 (
  echo.
  echo [ERROR] Launcher failed. See the message above.
  pause
)
exit /b 0
