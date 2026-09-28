@echo off
rem What the daily scheduled task runs. Not for double-clicking - there is no
rem pause at the end on purpose, so the window closes when the run is done.
rem
rem --scheduled is what lets it take a day off now and then. A manual run, from
rem the workbench or the command line, never skips.
cd /d "%~dp0"

set "PY="
where py >nul 2>&1 && set "PY=py"
if not defined PY where python >nul 2>&1 && set "PY=python"
if not defined PY exit /b 1

%PY% -u main.py --warm-all --scheduled
exit /b %ERRORLEVEL%
