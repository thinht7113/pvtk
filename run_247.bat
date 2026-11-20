@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PVTK 24/7 Bot Automation System

reg add HKCU\Console /v FaceName /t REG_SZ /d "Consolas" /f >nul 2>&1
reg add HKCU\Console /v CodePage /t REG_DWORD /d 65001 /f >nul 2>&1
reg add HKCU\Console /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ================================================================
echo       KHOI DONG HE THONG PVTK 24/7 BOT AUTOMATION
echo ================================================================
echo.

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 -X utf8 run_247.py
) else (
    where python3 >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        python3 -X utf8 run_247.py
    ) else (
        where python >nul 2>&1
        if %ERRORLEVEL% EQU 0 (
            python -X utf8 run_247.py
        ) else (
            echo [LOI] Khong tim thay Python tren he thong!
        )
    )
)

echo.
echo ================================================================
echo He thong da dung. Nhan phim bat ky de dong cua so...
pause
