@echo off
rem Run OG Checker from source (needs Python). Most people should just use OGChecker.exe.
cd /d "%~dp0"
python -c "import requests, webview" 2>nul || (
  echo Installing what OG Checker needs...
  python -m pip install -r requirements.txt
)
start "" pythonw app.py
