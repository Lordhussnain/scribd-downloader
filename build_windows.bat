@echo off
setlocal
cd /d "%~dp0"
where cargo >nul 2>nul
if errorlevel 1 (
    echo Cargo was not found. Install Rust with the x86_64-pc-windows-msvc toolchain.
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_portable.ps1"
if errorlevel 1 exit /b 1
exit /b 0
