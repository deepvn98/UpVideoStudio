@echo off
cd /d "%~dp0"
if exist "dist\UpVideoStudio.exe" (
    "dist\UpVideoStudio.exe"
) else (
    python run.py
)
if errorlevel 1 pause
