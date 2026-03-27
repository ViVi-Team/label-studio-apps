@echo off
setlocal

:: Get the directory where this batch file is located
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set LOCAL_FILES_SERVING_ENABLED=true
set LOCAL_FILES_DOCUMENT_ROOT=%SCRIPT_DIR%

:: Kill any existing server on port 8081
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8081 ^| findstr LISTENING') do taskkill /PID %%a /F 2>nul

:: Generate files.txt with image URLs
echo Scanning images...
dir /b /s "%SCRIPT_DIR%\images\*.jpg" > "%SCRIPT_DIR%\files.txt" 2>nul
powershell -Command "(Get-Content '%SCRIPT_DIR%\files.txt') -replace '%SCRIPT_DIR:\\=\%\\images\\', 'http://localhost:8081/' | Set-Content '%SCRIPT_DIR%\files.txt'"

:: Start the image server with CORS on port 8081
cd /d "%SCRIPT_DIR%\images"
start /b cmd /c "python \"%SCRIPT_DIR%\server.py\" 8081"

:: Wait a moment for server to start
timeout /t 2 >nul

:: Start Label Studio
label-studio start curating_mri_bboxes --init --label-config="%SCRIPT_DIR%\config.xml"