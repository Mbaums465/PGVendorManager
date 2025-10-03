@echo off
REM Navigate to the folder where the batch file is located
cd /d "%~dp0"

REM Run the Python script. I actually call this the file 'PGVendorTracker.py' on my PC but the file isnt named that here. yolo. 
python PGVendorManager.py

REM Optional: pause to keep the window open

pause
