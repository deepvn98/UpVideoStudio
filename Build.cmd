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
"%PYTHON_EXE%" -m PyInstaller --clean --noconfirm --onefile --windowed --name UpVideoStudio --add-data "web;web" run.py
if errorlevel 1 (
    pause
    exit /b 1
)
echo Da tao dist\UpVideoStudio.exe. Chi can gui file EXE nay.
