@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0.."

set PY_DIR=.python
set PY=%PY_DIR%\python.exe
set PY_VER=3.11.9
set PY_PTH=python311._pth
set SERVER=selab@aise.hknu.ac.kr
set PORT=51713

echo ============================================
echo   실습실 PC 초기 세팅
echo ============================================
echo.

REM ============================================
REM  0. OpenSSH 클라이언트 확인
REM ============================================
where ssh >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] OpenSSH 클라이언트가 설치되어 있지 않습니다.
    echo.
    echo   설정 ^> 앱 ^> 선택적 기능 ^> 기능 추가 ^> OpenSSH 클라이언트 설치
    echo.
    pause & exit /b 1
)

REM ============================================
REM  1. Portable Python 다운로드
REM ============================================
if exist "%PY%" (
    echo [1/4] Python 이미 설치됨: %PY%
    goto :packages
)

echo [1/4] Python %PY_VER% 다운로드 중...
curl -L --progress-bar -o _python.zip "https://www.python.org/ftp/python/%PY_VER%/python-%PY_VER%-embed-amd64.zip"
if %errorlevel% neq 0 (
    echo   [ERROR] 다운로드 실패. 인터넷 연결을 확인하세요.
    pause & exit /b 1
)

echo   압축 해제 중...
mkdir "%PY_DIR%" 2>nul
tar -xf _python.zip -C "%PY_DIR%"
del _python.zip

REM import site 활성화 (pip 필수) + src/scripts 경로 추가
(
echo python311.zip
echo .
echo ../src
echo ../scripts
echo import site
) > "%PY_DIR%\%PY_PTH%"

REM pip 설치
echo   pip 설치 중...
curl -sL -o "%PY_DIR%\get-pip.py" "https://bootstrap.pypa.io/get-pip.py"
"%PY%" "%PY_DIR%\get-pip.py" --no-warn-script-location --quiet 2>nul
del "%PY_DIR%\get-pip.py" 2>nul
echo   Python %PY_VER% 설치 완료!

:packages
REM ============================================
REM  2. 패키지 설치
REM ============================================
echo.
echo [2/4] 패키지 설치 중...
"%PY%" -m pip install --quiet "httpx>=0.27" "tenacity>=9.0" "pydantic>=2.7" "pydantic-settings>=2.3" "PyYAML>=6.0" "sqlalchemy>=2.0" 2>nul
"%PY%" -m pip install -e . --no-deps --quiet 2>nul
"%PY%" -c "import httpx, yaml, pydantic, tenacity, sqlalchemy" 2>nul
if %errorlevel% neq 0 (
    echo   [ERROR] 패키지 설치 실패
    pause & exit /b 1
)
echo   패키지 OK!

REM ============================================
REM  3. SSH 키 생성 + 서버 등록
REM ============================================
echo.
echo [3/4] SSH 설정...

if not exist "%USERPROFILE%\.ssh" mkdir "%USERPROFILE%\.ssh"

if not exist "%USERPROFILE%\.ssh\id_ed25519" (
    echo   SSH 키 생성 중...
    ssh-keygen -t ed25519 -f "%USERPROFILE%\.ssh\id_ed25519" -N "" -q
    echo   생성 완료!
) else (
    echo   SSH 키 이미 존재
)

echo.
echo   서버에 SSH 키를 등록합니다.
echo   비밀번호를 입력하세요:
type "%USERPROFILE%\.ssh\id_ed25519.pub" | ssh -p %PORT% -o StrictHostKeyChecking=no %SERVER% "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"

REM ============================================
REM  4. 연결 테스트
REM ============================================
echo.
echo [4/4] 연결 테스트...

ssh -p %PORT% -o StrictHostKeyChecking=no -o ConnectTimeout=10 %SERVER% "echo SSH_OK" 2>nul | findstr "SSH_OK" >nul
if %errorlevel% equ 0 (
    echo   SSH 연결 성공!
) else (
    echo   [ERROR] SSH 연결 실패. 네트워크/방화벽 확인 필요.
    pause & exit /b 1
)

echo   scp 테스트...
echo test > "%TEMP%\_scp_test.txt"
scp -P %PORT% -o StrictHostKeyChecking=no "%TEMP%\_scp_test.txt" %SERVER%:/tmp/_scp_test 2>nul
if %errorlevel% equ 0 (
    echo   scp 전송 성공!
    ssh -p %PORT% %SERVER% "rm -f /tmp/_scp_test" 2>nul
) else (
    echo   [ERROR] scp 실패.
)
del "%TEMP%\_scp_test.txt" 2>nul

echo.
echo ============================================
echo   세팅 완료!
echo.
echo   수집 시작: scripts\lab_collector.bat 더블클릭
echo   (PC 번호 1~41 입력)
echo ============================================
echo.
pause
