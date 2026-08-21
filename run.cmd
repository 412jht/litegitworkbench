@echo off
setlocal
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0litegit.py"
) else (
    python "%~dp0litegit.py"
)
endlocal
