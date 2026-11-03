@echo off
rem ---- Keep console and Python on the same encoding (UTF-8) ----
rem All executable lines are English on purpose: cmd parses batch files
rem line-by-line and Chinese echo lines can break parsing under chcp 65001.
rem Chinese users: see the "Install-Python-Guide.txt" next to this file.
chcp 65001 >nul
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

cd /d "%~dp0"

set PYEXE=

rem ---- Prefer the official launcher, then a real python.exe on PATH ----
where py >nul 2>nul
if not errorlevel 1 set PYEXE=py -3

if "%PYEXE%"=="" (
    for /f "delims=" %%i in ('where python 2^>nul') do (
        echo %%i | findstr /i "WindowsApps" >nul
        if errorlevel 1 (
            set "PYEXE=%%i"
            goto :found
        )
    )
)

:found
if "%PYEXE%"=="" (
    echo.
    echo   ============================================================
    echo   [!]  Python 3.10+ is required but was not detected.
    echo   ============================================================
    echo.
    echo   Opening the Python download page and a step-by-step guide...
    echo.
    echo   Step 1: download and run the installer
    echo   Step 2: CHECK the box "Add python.exe to PATH" on the first screen
    echo   Step 3: double-click this launcher again
    echo.
    echo   Offline installer is also available at:
    echo   github.com/yaohui686/Movie-Search/releases
    echo.
    pause
    start "" "https://www.python.org/downloads/"
    start "" notepad.exe "Install-Python-Guide.txt"
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
