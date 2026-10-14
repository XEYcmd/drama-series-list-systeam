@echo off
setlocal
cd /d "%~dp0"

rem  ONE-BUTTON launcher: server + LAN + phone access through your own domain (IPv6).
rem  Needs administrator rights (IPv6 portproxy + firewall) - relaunches itself elevated.
rem  ASCII only + CRLF on purpose.

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo Asking for administrator rights ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  where py >nul 2>nul && set "PYEXE=py"
)
if not defined PYEXE (
  echo.
  echo   [ERROR] Python not found. Install Python 3.9+ first.
  echo.
  pause
  exit /b 1
)

"%PYEXE%" "tools\launcher.py" --with-ipv6
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" echo   [launcher exit code: %RC%]
if not "%RC%"=="0" echo   If binding a port failed (WinError 10013), the Chinese lines above say why;
if not "%RC%"=="0" echo     Windows probably reserved that port - just run this file again.
echo   Done. You can close this window.
pause
