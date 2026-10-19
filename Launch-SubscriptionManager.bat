@echo off
setlocal enabledelayedexpansion
title Smart Ease Subscription Manager - Desktop Launcher
cd /d "%~dp0"

echo =========================================================================
echo                SMART EASE SUBSCRIPTION ^& REVENUE MANAGER                
echo               Professional Admin Console for Product Owner                
echo =========================================================================
echo.

set "PORT=8100"
set "APP_URL=http://127.0.0.1:%PORT%"
set "PYTHON_EXE=D:\Antigravity\photobook\python_env\python.exe"

if /i "%~1"=="/restart" (
    echo Terminating running server on port %PORT%...
    powershell -NoProfile -Command "$p = (Get-NetTCPConnection -LocalPort %PORT% -ErrorAction SilentlyContinue).OwningProcess; if ($p) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }" >nul 2>&1
    ping 127.0.0.1 -n 2 >nul
)

:: Step 1: Check if port 8100 is already active
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul
if %ERRORLEVEL% equ 0 (
    echo [OK] Backend server is already active on port %PORT%.
    goto LAUNCH_UI
)

:: Step 2: Start backend server
echo [1/2] Starting Smart Ease Subscription Manager backend server...
if exist "%PYTHON_EXE%" (
    start /min "SmartEase_SubscriptionManager" "%PYTHON_EXE%" "%~dp0server.py" %PORT%
) else (
    start /min "SmartEase_SubscriptionManager" python "%~dp0server.py" %PORT%
)

:: Step 3: Wait for server to become ready
echo [2/2] Waiting for server on port %PORT%...
set /a attempts=0
:WAIT_LOOP
ping 127.0.0.1 -n 2 >nul
set /a attempts+=1
netstat -ano | findstr ":%PORT% " | findstr "LISTENING" >nul
if %ERRORLEVEL% equ 0 goto LAUNCH_UI
if !attempts! geq 10 goto LAUNCH_UI
goto WAIT_LOOP

:LAUNCH_UI
echo Launching Smart Ease Subscription Manager in iMac Desktop Window Mode...

:: Launch Microsoft Edge in dedicated app window mode (zero browser tabs/address bar)
start "" msedge --app="%APP_URL%" --window-size=1560,960 >nul 2>&1
if %ERRORLEVEL% neq 0 (
    start "" chrome --app="%APP_URL%" --window-size=1560,960 >nul 2>&1
    if %ERRORLEVEL% neq 0 (
        start "" "%APP_URL%"
    )
)

echo.
echo =========================================================================
echo   SUCCESS: Smart Ease Subscription Manager is open!
echo   URL: %APP_URL%
echo =========================================================================
exit /b 0
