@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0.."

REM Portable Python 우선, 없으면 시스템 Python
if exist ".python\python.exe" (
    set PY=.python\python.exe
    goto :found
)

for %%P in (python python3 py) do (
    %%P -c "import sys; sys.exit(0)" >nul 2>&1
    if not errorlevel 1 (
        set PY=%%P
        goto :found
    )
)

echo [ERROR] Python을 찾을 수 없습니다. 먼저 lab_setup.bat을 실행하세요.
pause
exit /b 1

:found
REM 인자가 있으면 그대로 전달
if not "%~1"=="" (
    %PY% scripts\lab_collector.py --machine %*
    goto :done
)

REM 없으면 번호 입력받기
echo ============================================
echo   실습실 수집기
echo ============================================
echo.
set /p MACHINE_NUM="이 PC의 번호 (1~41): "
%PY% scripts\lab_collector.py --machine %MACHINE_NUM%

:done
echo.
pause
