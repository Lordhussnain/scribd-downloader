@echo off
setlocal
title Build Scribd Downloader Installer (Inno Setup)
cd /d "%~dp0"

echo ============================================================
echo  Scribd Downloader - Installer build (Inno Setup)
echo ============================================================
echo.

rem --- Locate the Inno Setup compiler (ISCC.exe) ---
set "ISCC="
where ISCC >nul 2>nul && set "ISCC=ISCC"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"

if not defined ISCC (
  echo [ERROR] Inno Setup 6 was not found.
  echo Install it with one of:
  echo     winget install JRSoftware.InnoSetup
  echo     https://jrsoftware.org/isdl.php
  pause
  exit /b 1
)
echo Using Inno Setup compiler: %ISCC%
echo.

rem --- Build the portable .exe first (same steps as build_windows.bat) ---
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] Python was not found on PATH.
  echo Install Python 3.10+ from https://www.python.org/downloads/
  pause
  exit /b 1
)

echo [1/3] Installing / updating dependencies and regenerating icons...
python -m pip install -r requirements.txt pyinstaller
if errorlevel 1 goto :failed
python assets\make_icon.py
if errorlevel 1 goto :failed

echo.
echo [2/3] Building dist\ScribdDownloader.exe with PyInstaller...
if exist build rmdir /s /q build
if exist dist\ScribdDownloader.exe del /q dist\ScribdDownloader.exe
python -m PyInstaller --noconfirm ScribdDownloader.spec
if errorlevel 1 goto :failed
dist\ScribdDownloader.exe --check
if errorlevel 1 (
  echo [ERROR] The built .exe failed its self-check - see output above.
  goto :failed
)

echo.
echo [3/3] Compiling the installer with Inno Setup...
"%ISCC%" installer\ScribdDownloader.iss
if errorlevel 1 goto :failed

echo.
echo ============================================================
echo  Done: dist\installer\ScribdDownloader-Setup-*.exe
echo  Share that single file: it installs per-user (no admin),
echo  adds Start-menu shortcuts and a clean uninstaller.
echo  Target PCs need Google Chrome installed.
echo ============================================================
pause
exit /b 0

:failed
echo.
echo [ERROR] Installer build failed - see the output above.
pause
exit /b 1
