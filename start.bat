@echo off
rem Start Tavern on Windows. Installs dependencies on first run.
rem Arguments go to `python -m tavern`, e.g. start.bat --sandbox --port 9000
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo ==^> Creating virtualenv in .venv
    python -m venv .venv || (echo Python 3.11+ is required: https://www.python.org/downloads/ & exit /b 1)
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q --only-binary=:all: -r requirements.txt || exit /b 1
)

".venv\Scripts\python.exe" -m tavern %*
