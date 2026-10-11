@echo off
setlocal
cd /d "%~dp0"

rem  Let the phone reach this PC over public IPv6 (bypasses Cloudflare completely).
rem  Windows portproxy: listen on IPv6 :PORT and forward to the IPv4 server.
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
echo Forwarding IPv6 :%PORT% to 127.0.0.1:%PORT% ...
netsh interface portproxy delete v6tov4 listenport=%PORT% listenaddress=:: >nul 2>&1
netsh interface portproxy add v6tov4 listenport=%PORT% listenaddress=:: connectport=%PORT% connectaddress=127.0.0.1
netsh advfirewall firewall delete rule name="worklib %PORT% IPv6" >nul 2>&1
netsh advfirewall firewall add rule name="worklib %PORT% IPv6" dir=in action=allow protocol=TCP localport=%PORT% profile=any

set "PYEXE="
where python >nul 2>nul && set "PYEXE=python"
if not defined PYEXE (
  where py >nul 2>nul && set "PYEXE=py"
)
if defined PYEXE "%PYEXE%" "tools\ipv6url.py" %PORT%
echo.
if exist ".cf-api-token.txt" (
  if defined PYEXE "%PYEXE%" "tools\cf_dns.py" --name lib.example.com
) else (
  echo   [skip] No .cf-api-token.txt - domain record not updated.
  echo          Create an API token (Edit zone DNS + Zone Read) and save it there,
  echo          then run this file again to point lib.example.com at this PC.
)
echo.
echo Keep the server window open, then open the http://[....]:%PORT% URL ON YOUR PHONE.
pause
