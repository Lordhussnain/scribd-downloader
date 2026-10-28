@echo off
setlocal
cd /d "%~dp0"
where cargo >nul 2>nul
if errorlevel 1 (
    echo Cargo was not found. Install Rust from https://rustup.rs/ and restart this shell.
    exit /b 1
)
cargo build --release --manifest-path rust-gui\Cargo.toml
if errorlevel 1 exit /b 1
echo.
echo Build complete: rust-gui\target\release\scribd-rust-gui.exe
echo Chrome and a compatible ChromeDriver are required to download documents.
exit /b 0
