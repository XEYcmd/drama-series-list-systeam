@echo off
setlocal
cd /d "%~dp0"

rem  Diagnose LAN access problems (why 192.168.x.x:7878 is unreachable).
rem  ASCII only + CRLF on purpose: cmd parses .bat in the active code page.

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

"%PYEXE%" "tools\diagnose.py"
echo.
pause
