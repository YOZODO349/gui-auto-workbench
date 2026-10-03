@echo off
chcp 936 >nul
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (".venv\Scripts\python.exe" runner.py --selftest) else (python runner.py --selftest)
pause
