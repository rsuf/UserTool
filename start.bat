@echo off
cd /d "%~dp0"
python -c "import requests" 2>nul || (
  echo Installing the one thing this needs ^(requests^)...
  python -m pip install requests
)
python server.py
pause
