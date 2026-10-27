@echo off
setlocal
where cl >nul 2>nul
if not errorlevel 1 goto build
set "LITEGIT_VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
if not exist "%LITEGIT_VSWHERE%" goto no_compiler
set "LITEGIT_VISUAL_STUDIO="
for /f "usebackq tokens=*" %%i in (`"%LITEGIT_VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "LITEGIT_VISUAL_STUDIO=%%i"
if not defined LITEGIT_VISUAL_STUDIO goto no_compiler
call "%LITEGIT_VISUAL_STUDIO%\Common7\Tools\VsDevCmd.bat" -no_logo -arch=x64 -host_arch=x64
if errorlevel 1 exit /b 1

:build
cd /d "%~dp0.."
python "%~dp0build_release.py" %*
exit /b %errorlevel%

:no_compiler
echo The MSVC x64 compiler and Windows SDK are required for the native launcher.
exit /b 1
