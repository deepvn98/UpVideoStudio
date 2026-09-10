@echo off
setlocal
cd /d "%~dp0"
set "PYTHON_EXE=python"
if exist "%LocalAppData%\Programs\Python\Python312\python.exe" set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python312\python.exe"
"%PYTHON_EXE%" -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo Khong tim thay Python va PyInstaller. Cai requirements-build.txt truoc khi build.
    pause
    exit /b 1
)
"%PYTHON_EXE%" -m PyInstaller --clean --noconfirm --onedir --windowed --name UpVideoStudio --add-data "web;web" run.py
if errorlevel 1 (
    pause
    exit /b 1
)
powershell -NoProfile -Command "Compress-Archive -LiteralPath 'dist\UpVideoStudio' -DestinationPath 'UpVideoStudio-Windows.zip' -Force"
if errorlevel 1 (
    pause
    exit /b 1
)
echo Da tao UpVideoStudio-Windows.zip va UpVideoStudio-Windows.sha256.txt
powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'UpVideoStudio-Windows.zip').Hash | Set-Content -Encoding ascii 'UpVideoStudio-Windows.sha256.txt'"
if errorlevel 1 (
    pause
    exit /b 1
)
