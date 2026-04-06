@echo off
net session >nul 2>&1
if %errorlevel% neq 0 (
    powershell -Command "Start-Process cmd -ArgumentList '/c cd /d \"%~dp0\" && powershell -ExecutionPolicy Bypass -File \"%~dp0scripts\setup_remote.ps1\"' -Verb RunAs"
    exit /b
)
powershell -ExecutionPolicy Bypass -File "%~dp0scripts\setup_remote.ps1"
