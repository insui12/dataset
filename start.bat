@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

set PY_DIR=.python
set PY=%PY_DIR%\python.exe
set PY_VER=3.11.9
set PY_PTH=python311._pth
set SERVER=selab@aise.hknu.ac.kr
set PORT=51713

echo ============================================
echo   SELAB Bug Report Collector
echo ============================================
echo.

REM ============================================
REM  0. OpenSSH check
REM ============================================
where ssh >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] OpenSSH not installed.
    echo   Settings ^> Apps ^> Optional Features ^> OpenSSH Client
    pause
    exit /b 1
)

REM ============================================
REM  1. Portable Python
REM ============================================
if exist "%PY%" (
    echo [1/5] Python OK
    goto :packages
)

echo [1/5] Downloading Python %PY_VER%...
curl -L --progress-bar -o _python.zip "https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-embed-amd64.zip"
if %errorlevel% neq 0 (
    echo [ERROR] Download failed.
    pause
    exit /b 1
)

echo   Extracting...
mkdir "%PY_DIR%" 2>nul
tar -xf _python.zip -C "%PY_DIR%"
del _python.zip

(
echo python311.zip
echo .
echo ../src
echo ../scripts
echo import site
) > "%PY_DIR%\%PY_PTH%"

echo   Installing pip...
curl -sL -o "%PY_DIR%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
"%PY%" "%PY_DIR%\get-pip.py" --no-warn-script-location --quiet 2>nul
del "%PY_DIR%\get-pip.py" 2>nul
echo   Python %PY_VER% ready!

:packages
REM ============================================
REM  2. Packages
REM ============================================
echo.
echo [2/5] Installing packages...
"%PY%" -m pip install --quiet "httpx>=0.27" "tenacity>=9.0" "pydantic>=2.7" "pydantic-settings>=2.3" "PyYAML>=6.0" "sqlalchemy>=2.0" 2>nul
"%PY%" -m pip install -e . --no-deps --quiet 2>nul
"%PY%" -c "import httpx, yaml, pydantic, tenacity, sqlalchemy" 2>nul
if %errorlevel% neq 0 (
    echo [ERROR] Package install failed.
    pause
    exit /b 1
)
echo   Packages OK!

REM ============================================
REM  3. SSH key
REM ============================================
echo.
echo [3/5] SSH setup...

if not exist "%USERPROFILE%\.ssh" mkdir "%USERPROFILE%\.ssh"

if not exist "%USERPROFILE%\.ssh\id_ed25519" (
    echo   Generating SSH key...
    ssh-keygen -t ed25519 -f "%USERPROFILE%\.ssh\id_ed25519" -N "" -q
    echo   Done!
) else (
    echo   SSH key exists
)

REM ============================================
REM  4. SSH test
REM ============================================
echo.
echo [4/5] Testing SSH connection...

ssh -p %PORT% -o StrictHostKeyChecking=no -o ConnectTimeout=10 -o BatchMode=yes %SERVER% "echo SSH_OK" 2>nul | findstr "SSH_OK" >nul
if %errorlevel% equ 0 (
    echo   SSH OK!
    goto :collect
)

echo   SSH key not registered. Enter server password:
type "%USERPROFILE%\.ssh\id_ed25519.pub" | ssh -p %PORT% -o StrictHostKeyChecking=no %SERVER% "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"

ssh -p %PORT% -o StrictHostKeyChecking=no -o ConnectTimeout=10 %SERVER% "echo SSH_OK" 2>nul | findstr "SSH_OK" >nul
if %errorlevel% equ 0 (
    echo   SSH OK!
) else (
    echo [ERROR] SSH connection failed.
    pause
    exit /b 1
)

:collect
REM ============================================
REM  5. Start collection
REM ============================================
echo.
echo ============================================
echo   Setup complete! Starting collector...
echo ============================================
echo.

if not "%~1"=="" (
    echo   Machine: %~1
    "%PY%" scripts\lab_collector.py --machine %*
    goto :done
)

set /p MACHINE_NUM="Enter this PC number (1-41): "
"%PY%" scripts\lab_collector.py --machine %MACHINE_NUM%

:done
echo.
pause
