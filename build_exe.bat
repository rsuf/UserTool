@echo off
rem Builds OGChecker.exe on your own PC (needs Python). The GitHub build does this for you.
cd /d "%~dp0"
python -m pip install -r requirements.txt pyinstaller || goto :fail
python -m PyInstaller --noconfirm OGChecker.spec || goto :fail
echo.
echo Done! Your app is in the "dist" folder: dist\OGChecker.exe
pause
exit /b 0
:fail
echo Build failed - see the messages above.
pause
