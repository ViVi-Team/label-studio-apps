@echo off
setlocal

:: Get the directory where this batch file is located
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: Kill any existing servers on ports 8081 and 8080
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8081 ^| findstr LISTENING') do taskkill /PID %%a /F 2>nul
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8080 ^| findstr LISTENING') do taskkill /PID %%a /F 2>nul

:: Start the image server with CORS on port 8081 from results/all_previews/
cd /d "%SCRIPT_DIR%\results\all_previews"
start /b cmd /c "python \"%SCRIPT_DIR%\server.py\" 8081"

:: Wait a moment for server to start
timeout /t 2 >nul

:: Start Label Studio
label-studio start checking_perturb_bboxes --init --label-config="%SCRIPT_DIR%\config.xml" --username defaultuser --password badpassword