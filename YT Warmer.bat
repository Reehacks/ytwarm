@echo off
setlocal
title YT Warmer  --  close this window to stop the server
cd /d "%~dp0"

rem The workbench opens your browser itself, so there is no "start http://..."
rem here - that would open the page twice. Port 8650 on purpose: 8000 is
rem ClipRank, 8188 ComfyUI, 8420 the Clip Gallery, 8500 Studio, 8600 Reddit
rem Shorts, 8750 Longform.

set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY goto :nopython
%PY% --version >nul 2>&1 || goto :nopython

rem Playwright is the only dependency. Checking here turns a traceback on the
rem first Warm click into one line and a fix.
%PY% -c "import playwright" >nul 2>&1
if errorlevel 1 (
  echo Playwright is missing - installing it now, this happens once.
  %PY% -m pip install --quiet playwright || goto :noplaywright
  echo Playwright installed.
)
echo.

rem -u keeps the output unbuffered, so this window shows the session log live.
%PY% -u ytwarm\app.py %*
set "RC=%ERRORLEVEL%"

echo.
if "%RC%"=="0" (echo YT Warmer stopped.) else (echo YT Warmer exited with code %RC%.)
echo.
pause
exit /b %RC%

:nopython
echo.
echo Python was not found on this machine.
echo   Install it from https://python.org and tick "Add Python to PATH".
echo.
pause
exit /b 1

:noplaywright
echo.
echo Could not install Playwright automatically. Run this yourself:
echo.
echo   %PY% -m pip install playwright
echo.
pause
exit /b 1
