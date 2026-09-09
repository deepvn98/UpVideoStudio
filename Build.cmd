@echo off
cd /d "%~dp0"
python -m PyInstaller --clean --noconfirm --onedir --console --name UpVideoStudio --add-data "web;web" run.py
if errorlevel 1 (
    pause
    exit /b 1
)
powershell -NoProfile -Command "Compress-Archive -LiteralPath 'dist\UpVideoStudio' -DestinationPath 'UpVideoStudio-Windows.zip' -Force"
if errorlevel 1 (
    pause
    exit /b 1
)
