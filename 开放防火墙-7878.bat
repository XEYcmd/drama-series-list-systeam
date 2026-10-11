@echo off
setlocal
cd /d "%~dp0"

rem  Add an inbound firewall rule for the worklib port (default TCP 7878).
rem  Needs administrator rights - this file relaunches itself elevated.
rem  ASCII only + CRLF on purpose.

net session >nul 2>&1
if not "%errorlevel%"=="0" (
  echo Asking for administrator rights ...
  powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
  exit /b
)

set "PORT=7878"
if exist "server\config.json" (
  for /f "usebackq tokens=2 delims=:" %%p in (`findstr /i "port" "server\config.json"`) do (
    for /f "tokens=1 delims=, " %%q in ("%%p") do set "PORT=%%q"
  )
)

echo.
echo Allowing inbound TCP %PORT% on private + domain networks ...
netsh advfirewall firewall delete rule name="worklib %PORT%" >nul 2>&1
netsh advfirewall firewall add rule name="worklib %PORT%" dir=in action=allow protocol=TCP localport=%PORT% profile=private,domain
echo.
echo Done. If Windows still classifies your Wi-Fi as "Public", it stays blocked:
echo   Settings - Network and Internet - Wi-Fi - your network - network profile - Private
echo.
pause
