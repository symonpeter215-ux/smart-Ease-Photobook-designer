@echo off
setlocal
title Smart Ease Subscription & Revenue Manager

echo ===================================================================
echo  Smart Ease Subscription & Revenue Manager
echo  Connecting directly to Render Cloud Server:
echo  https://smart-ease-photobook-designer.onrender.com
echo  (Zero local server required - live 24/7 cloud connection)
echo ===================================================================

set CHROME_BIN=
if exist "%ProgramFiles%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_BIN=%ProgramFiles%\Google\Chrome\Application\chrome.exe"
) else if exist "%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_BIN=%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"
) else if exist "%LocalAppData%\Google\Chrome\Application\chrome.exe" (
    set "CHROME_BIN=%LocalAppData%\Google\Chrome\Application\chrome.exe"
)

set EDGE_BIN=
if exist "%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe" (
    set "EDGE_BIN=%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"
) else if exist "%ProgramFiles%\Microsoft\Edge\Application\msedge.exe" (
    set "EDGE_BIN=%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"
)

set "TARGET_URL=https://smart-ease-photobook-designer.onrender.com/static/index.html"

if defined CHROME_BIN (
    start "" "%CHROME_BIN%" --app="%TARGET_URL%" --window-size=1440,920
) else if defined EDGE_BIN (
    start "" "%EDGE_BIN%" --app="%TARGET_URL%" --window-size=1440,920
) else (
    start "" "%TARGET_URL%"
)

endlocal
