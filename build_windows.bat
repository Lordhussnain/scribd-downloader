@echo off
setlocal
title Build Scribd Downloader (Windows .exe)
cd /d "%~dp0"

echo ============================================================
echo  Scribd Downloader - Windows build
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found on PATH.
  echo Install Python 3.10+ from https://www.python.org/downloads/
  echo and tick "Add python.exe to PATH" during setup.
  pause
  exit /b 1
)

echo [1/4] Installing / updating dependencies...
python -m pip install --upgrade pip
if errorlevel 1 goto :failed
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :failed

echo.
echo [2/4] Regenerating the app icon...
python assets\make_icon.py
if errorlevel 1 goto :failed

echo.
echo [3/4] Building ScribdDownloader.exe with PyInstaller...
if exist build rmdir /s /q build
if exist dist\ScribdDownloader.exe del /q dist\ScribdDownloader.exe
python -m PyInstaller --noconfirm ScribdDownloader.spec
if errorlevel 1 goto :failed

echo.
echo [4/4] Verifying the built executable...
dist\ScribdDownloader.exe --check
if errorlevel 1 (
  echo [WARNING] The .exe self-check reported a problem - see output above.
) else (
  echo Self-check passed.
)

echo.
echo ============================================================
echo  Done:  dist\ScribdDownloader.exe
echo  You can copy this single file anywhere and double-click it.
echo  Google Chrome must be installed on the target PC.
echo ============================================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Build failed - see the output above.
pause
exit /b 1
