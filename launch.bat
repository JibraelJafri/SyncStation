@echo off
title SyncStation - Multi-Platform Music Sync Engine
cd /d "%~dp0"
echo ========================================================
echo  SyncStation: Spotify -^> YouTube Music / Deezer Engine
echo ========================================================
echo.
echo Starting SyncStation...
".venv\Scripts\python.exe" "app.py"
if %errorlevel% neq 0 (
    echo.
    echo An error occurred while starting SyncStation.
    pause
)

