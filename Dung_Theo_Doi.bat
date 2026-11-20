@echo off
chcp 65001 >nul
title Dung Theo Doi So Giao Dich

echo ================================================================
echo       DUNG TIEN TRINH THEO DOI SO GIAO DICH CHAY NGAM
echo ================================================================
echo.

powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*character_market_watcher.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force; Write-Host '-> Da dung tien trinh PID:' $_.ProcessId }"

echo.
echo [THANH CONG] Da dung theo doi So Giao Dich.
timeout /t 3 >nul
