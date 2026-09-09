@echo off
cd /d "%~dp0"
if exist "dist\UpVideoStudio\UpVideoStudio.exe" (
    "dist\UpVideoStudio\UpVideoStudio.exe"
) else (
    python run.py
)
if errorlevel 1 pause
