@echo off
setlocal
title Scribd Downloader
cd /d "%~dp0"
rem Launch the GUI from source without a console window.
rem If pythonw is unavailable, fall back to python.
if exist "%LocalAppData%\Programs\Python" (
  for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
    if exist "%%D\pythonw.exe" (
      start "" "%%D\pythonw.exe" "%~dp0gui_app.py"
      exit /b 0
    )
  )
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw "%~dp0gui_app.py"
  exit /b 0
)
python gui_app.py
if errorlevel 1 pause
