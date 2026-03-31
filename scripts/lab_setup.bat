@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0.."

echo === Python 확인 중... ===

REM Windows에서 python이 Microsoft Store로 리다이렉트되는 문제 해결
REM 실제 Python이 설치되어 있는지 확인
python --version >nul 2>&1
if %errorlevel% equ 0 (
    REM python이 실행되지만 Store 앱 리다이렉트인지 확인
    python -c "import sys; sys.exit(0)" >nul 2>&1
    if %errorlevel% equ 0 (
        set PY=python
        goto :found
    )
)

python3 --version >nul 2>&1
if %errorlevel% equ 0 (
    python3 -c "import sys; sys.exit(0)" >nul 2>&1
    if %errorlevel% equ 0 (
        set PY=python3
        goto :found
    )
)

py --version >nul 2>&1
if %errorlevel% equ 0 (
    set PY=py
    goto :found
)

echo.
echo [ERROR] Python을 찾을 수 없습니다.
echo.
echo 해결 방법:
echo   1. https://python.org/downloads 에서 Python 3.11+ 설치
echo   2. 설치 시 "Add Python to PATH" 반드시 체크
echo   3. 설정 ^> 앱 ^> 앱 실행 별칭 에서 "python.exe" 앱 설치 관리자 끄기
echo.
pause
exit /b 1

:found
echo   Python 발견: %PY%
echo.
%PY% scripts\lab_setup.py
echo.
pause
