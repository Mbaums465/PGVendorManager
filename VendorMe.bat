@echo off
REM Vendor Money Tracker Launcher
setlocal

set "MAIN_SCRIPT=PGVendorTracker.py"
cd /d "%~dp0"

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo Python not found. Installing Python...
    call :install_python
    REM After install, we need to restart the batch file to get new PATH
    echo Restarting launcher...
    timeout /t 2 /nobreak >nul
    start "" "%~f0"
    exit /b 0
)

REM Check if the main script exists
if not exist "%MAIN_SCRIPT%" (
    echo ERROR: Could not find %MAIN_SCRIPT%
    pause
    exit /b 1
)

REM Install required packages
echo Checking Python packages...
python -m pip install --upgrade pip --quiet 2>nul
python -m pip install -r requirements.txt --quiet 2>nul

:run_script
REM Use full path to pythonw or fallback to python
where pythonw >nul 2>&1
if %errorlevel% equ 0 (
    start /b "" pythonw "%MAIN_SCRIPT%"
) else (
    REM Fallback: run with python in background
    start /b "" python "%MAIN_SCRIPT%"
)
exit /b 0

:install_python
echo Downloading Python installer (1-2 minutes)...
powershell -Command "$ProgressPreference = 'SilentlyContinue'; Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe' -OutFile 'python-installer.exe'"

if exist "python-installer.exe" (
    echo Installing Python (2-3 minutes)... Please wait.
    start /wait python-installer.exe /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
    del python-installer.exe
    echo Python installation complete!
) else (
    echo ERROR: Failed to download Python installer.
    pause
    exit /b 1
)
exit /b 0
