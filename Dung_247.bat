@echo off
chcp 65001 >nul
title Dung PVTK 24/7 Bot

echo ================================================================
echo       DUNG TIEN TRINH PVTK 24/7 BOT CHAY NGAM
echo ================================================================
echo.

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*run_247.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host '-> Da dung tien trinh 24/7 PID:' $_.ProcessId }"

echo.
echo [THANH CONG] Da dung toan bo he thong PVTK 24/7 Bot.
timeout /t 3 >nul
