@echo off
REM Vendor Money Tracker Launcher (Silent Mode)
REM This batch file automatically sets up Python and runs the vendor tracker

setlocal

REM Set the main Python script name here - change this if you rename your script
set "MAIN_SCRIPT=PGVendorTracker.py"

REM Navigate to the folder where the batch file is located
cd /d "%~dp0"

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    REM Python not found - need to show window for installation
    echo ========================================
    echo   Vendor Money Tracker Launcher
    echo ========================================
    echo.
    echo Python not found. Installing Python...
    call :install_python
    goto :run_script
)

REM Check if the main script exists
if not exist "%MAIN_SCRIPT%" (
    REM Show error window
    echo ========================================
    echo   Vendor Money Tracker Launcher
    echo ========================================
    echo.
    echo ERROR: Could not find %MAIN_SCRIPT%
    echo.
    echo Please make sure the Python script is in the same folder as this batch file.
    echo If you renamed the script, please update the MAIN_SCRIPT variable in this batch file.
    echo.
    pause
    exit /b 1
)

:run_script
REM Run the Python script silently (no CMD window output)
start /b "" pythonw "%MAIN_SCRIPT%"

REM Exit immediately without keeping window open
exit /b 0

:install_python
REM Download and install Python silently
echo Downloading Python installer...
powershell -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile 'python-installer.exe'"

if exist "python-installer.exe" (
    echo Installing Python... This may take a few minutes.
    python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    del python-installer.exe
    echo Python installation complete!
    
    REM Refresh environment variables
    set PATH=%PATH%;C:\Python311;C:\Python311\Scripts
    pause
) else (
    echo ERROR: Failed to download Python installer.
    echo Please install Python manually from https://www.python.org/downloads/
    pause
    exit /b 1
)

exit /b 0
