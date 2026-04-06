@echo off
chcp 65001 >nul 2>&1
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Run as Administrator
    echo   Right-click ^> Run as administrator
    pause
    exit /b 1
)

echo ============================================
echo   Lab PC Remote Control Setup
echo ============================================
echo.

REM 1. Set password
echo [1/4] Setting password...
net user %USERNAME% selab1234
if %errorlevel% neq 0 (
    echo [ERROR] Password set failed
    pause
    exit /b 1
)
echo   Password set: selab1234

REM 2. Network to Private + Enable WinRM
echo.
echo [2/5] Setting network to Private...
powershell -Command "Get-NetConnectionProfile | Where-Object {$_.NetworkCategory -eq 'Public'} | Set-NetConnectionProfile -NetworkCategory Private" >nul 2>&1
echo   Network set to Private

echo.
echo [3/5] Enabling WinRM...
winrm quickconfig -quiet
powershell -Command "Enable-PSRemoting -Force -SkipNetworkProfileCheck" >nul 2>&1
echo   WinRM enabled

REM 4. Firewall
echo.
echo [4/5] Firewall rules...
netsh advfirewall firewall add rule name="WinRM-HTTP" dir=in action=allow protocol=TCP localport=5985 >nul 2>&1
netsh advfirewall firewall add rule name="WOL" dir=in action=allow protocol=UDP localport=9 >nul 2>&1
echo   Firewall OK

REM 5. Disable sleep
echo.
echo [5/5] Disabling sleep...
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /change monitor-timeout-ac 0
echo   Sleep disabled

REM Save this PC's info
echo.
for /f "tokens=2 delims=:" %%i in ('ipconfig ^| findstr /i "IPv4" ^| findstr "10.108"') do set MYIP=%%i
set MYIP=%MYIP: =%
for /f %%i in ('getmac /fo csv /nh ^| findstr /i "-"') do set MYMAC=%%i
echo   IP:  %MYIP%
echo   MAC: %MYMAC%

echo.
echo ============================================
echo   Setup complete!
echo   IP:  %MYIP%
echo   MAC: %MYMAC%
echo ============================================
echo.
pause
