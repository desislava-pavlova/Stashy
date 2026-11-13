@echo off
rem Every check to run before pushing: lint, formatting, then the full test suite.
rem Double-click it or run it from this folder. The tests use their own throwaway database,
rem so this never touches a stash.
setlocal
cd /d "%~dp0"
set "FAILED=0"

echo === ruff ===
.venv\Scripts\ruff.exe check . || set "FAILED=1"
.venv\Scripts\ruff.exe format --check . || set "FAILED=1"

echo.
echo === tests ===
.venv\Scripts\python.exe manage.py test || set "FAILED=1"

if "%FAILED%"=="1" (echo. & echo SOME CHECKS FAILED) else (echo. & echo ALL CHECKS PASSED)
pause
exit /b %FAILED%
