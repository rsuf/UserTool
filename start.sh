#!/bin/sh
cd "$(dirname "$0")"
python3 -c "import requests" 2>/dev/null || python3 -m pip install requests
python3 server.py
