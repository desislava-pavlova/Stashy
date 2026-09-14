@echo off
setlocal
cd /d "%~dp0"
title Stashy

set "VENV_PY=.venv\Scripts\python.exe"

rem A .venv copied from another computer points at a Python that doesn't exist here.
if exist "%VENV_PY%" (
    "%VENV_PY%" -c "import sys" >nul 2>&1 || (
        echo The existing .venv folder doesn't work on this computer. Rebuilding it...
        rmdir /s /q .venv
    )
)

if not exist "%VENV_PY%" (
    call :find_python
    if not defined BASE_PY goto :no_python
    echo Creating Stashy's private Python environment ^(one time only, takes a minute^)...
    call %%BASE_PY%% -m venv .venv
    if errorlevel 1 goto :failed
)

"%VENV_PY%" scripts\bootstrap.py
if errorlevel 1 goto :failed
exit /b 0


:find_python
set "BASE_PY="
for %%C in ("py -3.14" "py -3.13" "py -3.12" "python") do (
    if not defined BASE_PY (
        %%~C -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1 && set "BASE_PY=%%~C"
    )
)
exit /b 0


:no_python
echo.
echo  Stashy needs Python 3.12 or newer, and it isn't installed on this computer yet.
echo.
echo    1. Go to https://www.python.org/downloads/ and download the latest Python 3.
echo    2. Run the installer and TICK "Add python.exe to PATH" on the first screen.
echo    3. Double-click Stashy.bat again.
echo.
pause
exit /b 1


:failed
echo.
echo  Stashy stopped. If that wasn't you closing it, scroll up to see what went wrong.
echo.
pause
exit /b 1
