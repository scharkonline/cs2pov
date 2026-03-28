@echo off
REM Build cs2pov.exe for Windows distribution
REM Run this on a Windows machine with Python 3.10+ installed
REM Output: dist\cs2pov.exe

setlocal

echo === cs2pov Windows Build ===
echo.

REM Check Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Install Python 3.10+ from python.org
    exit /b 1
)

REM Create a clean virtual environment
echo [1/4] Creating virtual environment...
if exist build_venv rmdir /s /q build_venv
python -m venv build_venv
call build_venv\Scripts\activate.bat

REM Install all dependencies
echo [2/4] Installing dependencies...
pip install --upgrade pip >nul
pip install .[gui,windows] pyinstaller

if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    exit /b 1
)

REM Build the exe
echo [3/4] Building cs2pov.exe...
pyinstaller cs2pov.spec --noconfirm

if errorlevel 1 (
    echo ERROR: PyInstaller build failed
    exit /b 1
)

REM Verify output
echo [4/4] Verifying build...
if exist dist\cs2pov.exe (
    echo.
    echo === Build successful ===
    echo Output: dist\cs2pov.exe
    for %%A in (dist\cs2pov.exe) do echo Size: %%~zA bytes
) else (
    echo ERROR: dist\cs2pov.exe not found
    exit /b 1
)

REM Cleanup venv
echo.
echo Cleaning up build environment...
call deactivate
rmdir /s /q build_venv

echo Done.
