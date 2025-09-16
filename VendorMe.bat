@echo off
REM Navigate to the folder where the batch file is located
cd /d "%~dp0"

REM Run the Python script
python PGVendorTracker.py

REM Optional: pause to keep the window open
pause