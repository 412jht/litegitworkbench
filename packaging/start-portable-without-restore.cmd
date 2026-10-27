@echo off
setlocal
cd /d "%~dp0"
set "LITEGIT_STARTUP_LOG=%LOCALAPPDATA%\LiteGitWorkbench\diagnostics\startup-python-%RANDOM%-%RANDOM%.jsonl"
call "%~dp0run-portable.cmd" --no-restore %*
exit /b %errorlevel%
