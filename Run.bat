@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0start.py" launch
  if errorlevel 1 pause
  exit /b
)
where python >nul 2>nul
if not errorlevel 1 (
  python "%~dp0start.py" launch
  if errorlevel 1 pause
  exit /b
)
powershell -NoProfile -Command "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('ADA Labs needs Python 3.11 or newer. Install Python, then open Run.bat again.','ADA Labs')"
