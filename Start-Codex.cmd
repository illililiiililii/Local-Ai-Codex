@echo off
setlocal
cd /d "%~dp0"
set "PS_EXE="
if exist "%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" set "PS_EXE=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not defined PS_EXE if exist "%ProgramFiles%\PowerShell\7\pwsh.exe" set "PS_EXE=%ProgramFiles%\PowerShell\7\pwsh.exe"
if not defined PS_EXE for /f "delims=" %%P in ('where pwsh.exe 2^>nul') do if not defined PS_EXE set "PS_EXE=%%P"
if not defined PS_EXE (
    echo PowerShell was not found. Windows PowerShell or PowerShell 7 is required.
    echo Install PowerShell, then double-click this file again.
    set "RC=9009"
    pause
    exit /b 9009
)
"%PS_EXE%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0LocalCodex.ps1"
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" (
    echo.
    echo Local Codex setup failed. Exit code: %RC%
    if exist "%~dp0bootstrap-error.log" echo Details: "%~dp0bootstrap-error.log"
    pause
)
exit /b %RC%
