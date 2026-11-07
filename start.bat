@echo off
rem Start Tavern on Windows. Installs dependencies on first run.
rem Arguments go to `python -m tavern`, e.g. start.bat --sandbox --port 9000
rem The Update button on the page ends the app with exit code 75; the loop below then starts it again.
rem The loop is one command (cmd reads it whole before running), so an update replacing this file cannot
rem confuse the run in progress. (exit /b inside a FOR loses the exit code, hence the goto.)
setlocal enabledelayedexpansion
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ==^> Creating virtualenv in .venv
    python -m venv .venv || (echo Python 3.11+ is required: https://www.python.org/downloads/ & exit /b 1)
)

for /l %%i in (1,1,100000) do (
    rem Also after an update: the installed list is remembered and compared with requirements.txt.
    fc /b requirements.txt ".venv\requirements.installed" >nul 2>nul || (
        echo ==^> Installing dependencies
        ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --only-binary=:all: -r requirements.txt || (set "CODE=1" & goto :fin)
        copy /y requirements.txt ".venv\requirements.installed" >nul
    )
    set TAVERN_SUPERVISED=1
    ".venv\Scripts\python.exe" -m tavern %*
    set "CODE=!errorlevel!"
    if not "!CODE!"=="75" goto :fin
    echo ==^> Restart after the update
    set TAVERN_RESTARTED=1
)
exit /b 0
:fin
exit /b !CODE!
