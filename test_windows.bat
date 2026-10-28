@echo off
setlocal
cd /d "%~dp0"
where cargo >nul 2>nul
if errorlevel 1 (
  echo Cargo was not found. Install the Rust MSVC toolchain and restart PowerShell.
  exit /b 1
)
echo Running core tests...
cargo test --manifest-path rust-core\Cargo.toml
if errorlevel 1 exit /b 1
echo Running browser and PDF tests...
cargo test --manifest-path rust-browser\Cargo.toml
if errorlevel 1 exit /b 1
echo Running GUI settings and theme tests...
cargo test --manifest-path rust-gui\Cargo.toml
if errorlevel 1 exit /b 1
echo All automated tests passed.
