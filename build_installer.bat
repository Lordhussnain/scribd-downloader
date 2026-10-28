@echo off
setlocal
cd /d "%~dp0"
call build_windows.bat
if errorlevel 1 exit /b 1
set "ISCC="
where ISCC >nul 2>nul && set "ISCC=ISCC"
if not defined ISCC if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" set "ISCC=%ProgramFiles%\Inno Setup 6\ISCC.exe"
if not defined ISCC if exist "%LocalAppData%\Programs\Inno Setup 6\ISCC.exe" set "ISCC=%LocalAppData%\Programs\Inno Setup 6\ISCC.exe"
if not defined ISCC (
    echo Install Inno Setup 6 and ensure ISCC.exe is available.
    exit /b 1
)
"%ISCC%" installer\ScribdDownloader.iss
if errorlevel 1 exit /b 1
echo Installer created in dist\installer.
exit /b 0
