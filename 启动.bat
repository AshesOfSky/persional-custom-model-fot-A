@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Cleaning cache...
if exist __pycache__ rd /s /q __pycache__ 2>nul
if exist modules\__pycache__ rd /s /q modules\__pycache__ 2>nul
if exist plugins\__pycache__ rd /s /q plugins\__pycache__ 2>nul
python launch.py
echo.
pause
