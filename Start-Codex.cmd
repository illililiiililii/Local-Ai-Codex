@echo off
setlocal EnableExtensions EnableDelayedExpansion
set "PYW="
for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
    if exist "%%~fD\pythonw.exe" if exist "%%~fD\python.exe" (
        "%%~fD\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
        if not errorlevel 1 set "PYW=%%~fD\pythonw.exe"
    )
)
if not defined PYW (
    echo Python is required to open Local Codex AI. Installing Python 3.13...
    winget install --id Python.Python.3.13 --exact --source winget --accept-source-agreements --accept-package-agreements
    for /d %%D in ("%LocalAppData%\Programs\Python\Python*") do (
        if exist "%%~fD\pythonw.exe" if exist "%%~fD\python.exe" (
            "%%~fD\python.exe" -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
            if not errorlevel 1 set "PYW=%%~fD\pythonw.exe"
        )
    )
)
if not defined PYW (
    echo Python could not be located. Install Python 3.10 or later, then run this file again.
    pause
    exit /b 1
)
start "Local Codex AI" "!PYW!" "%~dp0Start-Codex.pyw"
