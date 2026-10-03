@echo off
chcp 936 >nul
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" goto RUN

set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"
%PY% --version >nul 2>nul
if errorlevel 1 goto NOPY

echo 首次运行：正在建虚拟环境并安装依赖（需要联网，约 1-2 分钟）……
%PY% -m venv .venv
if errorlevel 1 goto VENVFAIL
".venv\Scripts\python.exe" -m pip install --upgrade pip >nul 2>nul
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto PIPFAIL

:RUN
start "" ".venv\Scripts\pythonw.exe" gui.py
exit /b 0

:NOPY
echo [x] 没找到能用的 Python。
echo     请安装 Python 3.10 或更高版本，安装时务必勾选 tcl/tk 与 "Add python.exe to PATH"，然后重新双击本文件。
pause
exit /b 1

:VENVFAIL
echo [x] 建虚拟环境失败：多半是 Python 装得不完整（缺 venv 模块）。
pause
exit /b 1

:PIPFAIL
echo [x] 依赖安装失败。检查网络后重跑本文件；或手动执行：
echo     .venv\Scripts\python.exe -m pip install -r requirements.txt
pause
exit /b 1
