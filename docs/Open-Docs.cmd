@echo off
rem Opens the documentation viewer (needs Python). Double-click this file; close the window to stop it.
cd /d "%~dp0"
python serve.py 2>nul || py serve.py
if errorlevel 1 (
  echo.
  echo Python was not found. Install Python, or run:  python serve.py
  pause
)
