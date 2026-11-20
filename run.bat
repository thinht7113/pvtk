@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PVTK Headless Protocol Tool

:: Thiet lap font TrueType (Consolas) va ma hoa UTF-8 de tranh hoan toan loi dau ? tieng Viet tren Windows
reg add HKCU\Console /v FaceName /t REG_SZ /d "Consolas" /f >nul 2>&1
reg add HKCU\Console /v FontFamily /t REG_DWORD /d 54 /f >nul 2>&1
reg add HKCU\Console /v FontSize /t REG_DWORD /d 0x00100000 /f >nul 2>&1
reg add HKCU\Console /v FontWeight /t REG_DWORD /d 400 /f >nul 2>&1
reg add HKCU\Console /v CodePage /t REG_DWORD /d 65001 /f >nul 2>&1
reg add HKCU\Console /v VirtualTerminalLevel /t REG_DWORD /d 1 /f >nul 2>&1
reg add HKCU\Console /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1

reg add "HKCU\Console\PVTK Headless Protocol Tool" /v FaceName /t REG_SZ /d "Consolas" /f >nul 2>&1
reg add "HKCU\Console\PVTK Headless Protocol Tool" /v FontFamily /t REG_DWORD /d 54 /f >nul 2>&1
reg add "HKCU\Console\PVTK Headless Protocol Tool" /v FontSize /t REG_DWORD /d 0x00100000 /f >nul 2>&1
reg add "HKCU\Console\PVTK Headless Protocol Tool" /v FontWeight /t REG_DWORD /d 400 /f >nul 2>&1
reg add "HKCU\Console\PVTK Headless Protocol Tool" /v CodePage /t REG_DWORD /d 65001 /f >nul 2>&1
reg add "HKCU\Console\PVTK Headless Protocol Tool" /v VirtualTerminalLevel /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKCU\Console\PVTK Headless Protocol Tool" /v QuickEdit /t REG_DWORD /d 0 /f >nul 2>&1

set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1

echo ================================================================
echo       KHOI DONG HE THONG PVTK HEADLESS PROTOCOL TOOL
echo ================================================================
echo.

where py >nul 2>&1
if %ERRORLEVEL% EQU 0 (
    py -3 main.py
) else (
    where python >nul 2>&1
    if %ERRORLEVEL% EQU 0 (
        python main.py
    ) else (
        echo [LOI] Khong tim thay Python tren he thong!
    )
)

echo.
echo ================================================================
echo Chuong trinh da ket thuc. Nhan phim bat ky de dong cua so...
pause >nul