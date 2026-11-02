@echo off
rem ---- Keep console and Python on the same encoding (UTF-8) ----
rem If console is UTF-8 (65001) but Python reads stdin as GBK,
rem typing Chinese raises UnicodeDecodeError and the window closes.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"

set PYEXE=
where python >nul 2>nul
if not errorlevel 1 set PYEXE=python
if "%PYEXE%"=="" (
    where py >nul 2>nul
    if not errorlevel 1 set PYEXE=py -3
)

if "%PYEXE%"=="" (
    echo.
    echo   [ERROR] python not found.
    echo   Install Python 3.10+ and check "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

%PYEXE% run.py
set RC=%ERRORLEVEL%

if not "%RC%"=="0" (
    echo.
    echo   [ERROR] exit code %RC%
    echo   See logs\crash.log for details.
    echo.
    pause
)
