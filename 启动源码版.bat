@echo off
cd /d "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" app.py
) else (
    echo Please follow the source setup in README.md, or use the packaged AI-Sub-Maker.exe.
    pause
)
