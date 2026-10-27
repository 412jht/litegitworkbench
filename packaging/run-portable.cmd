@echo off
setlocal
cd /d "%~dp0"
set "LITEGIT_ARCHIVE=%~dp0LiteGitWorkbench.pyz"

where py >nul 2>nul
if not errorlevel 1 goto run_py

where python >nul 2>nul
if not errorlevel 1 goto run_python

echo LiteGit Workbench requires Python 3.10 or later with Tkinter.
echo Install an administrator-approved Python runtime, then try again.
pause
exit /b 1

:run_py
py -3 "%LITEGIT_ARCHIVE%" %*
exit /b %errorlevel%

:run_python
python "%LITEGIT_ARCHIVE%" %*
exit /b %errorlevel%
