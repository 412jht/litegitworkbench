@echo off
setlocal
set "LITEGIT_ARCHIVE=%~dp0LiteGitWorkbench.pyz"

where py >nul 2>nul
if not errorlevel 1 (
    py -3 "%LITEGIT_ARCHIVE%" %*
    exit /b %errorlevel%
)

where python >nul 2>nul
if not errorlevel 1 (
    python "%LITEGIT_ARCHIVE%" %*
    exit /b %errorlevel%
)

echo LiteGit Workbench requires Python 3.10 or later with Tkinter.
echo Install an administrator-approved Python runtime, then try again.
pause
exit /b 1
