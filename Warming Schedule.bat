@echo off
setlocal
title YT Warmer schedule
cd /d "%~dp0"

rem A wrapper so the schedule can be driven without fighting PowerShell's
rem execution policy. Double-click it for the current state, or run it with a
rem command:
rem
rem   "Warming Schedule.bat" install      create the daily task (10:00 + up to 4h)
rem   "Warming Schedule.bat" pause        stop it running, keep the task
rem   "Warming Schedule.bat" resume       start it running again
rem   "Warming Schedule.bat" run-now      run one warming round this second
rem   "Warming Schedule.bat" remove       delete the task
rem   "Warming Schedule.bat" status       what it is doing now (the default)

set "CMD=%~1"
if "%CMD%"=="" set "CMD=status"

powershell -NoProfile -ExecutionPolicy Bypass -File "ytwarm\schedule.ps1" %CMD% %2 %3 %4
echo.
if "%CMD%"=="status" (
  echo Commands: install ^| pause ^| resume ^| run-now ^| remove ^| status
  echo A different time:  "Warming Schedule.bat" install -At 19:30 -WindowHours 3
  echo.
)
pause
