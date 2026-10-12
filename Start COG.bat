@echo off
setlocal EnableExtensions EnableDelayedExpansion

rem ============================================================
rem  Start COG.bat
rem
rem  Double-click launcher for the Centre of Gravity GUI.
rem  First run: sets up a private Python environment (needs
rem  internet, takes a few minutes). Every run after that just
rem  opens the app in a few seconds.
rem
rem ============================================================

cd /d "%~dp0"
if errorlevel 1 (
    echo Could not switch to the COG folder. Please contact Omar.
    pause
    exit /b 1
)

set "REPO_DIR=%~dp0"
set "COG_HOME=%LOCALAPPDATA%\COG"
set "VENV_DIR=%COG_HOME%\.venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PYW=%VENV_DIR%\Scripts\pythonw.exe"
set "LOG=%COG_HOME%\setup.log"
set "MARKER=%COG_HOME%\.installed"
set "LOCK_FILE=%~dp0uv.lock"
set "LAUNCH_VBS=%COG_HOME%\launch.vbs"

rem cog itself is run straight from source (src\), not from an installed
rem package, so main.py can import it whether or not "uv sync" / pip ended
rem up installing the project as a package.
set "PYTHONPATH=%REPO_DIR%src;%PYTHONPATH%"

if not exist "%COG_HOME%" mkdir "%COG_HOME%" >nul 2>&1

call :log "===================================================="
call :log "Start COG.bat run at %date% %time%"
call :log "REPO_DIR=%REPO_DIR%"
call :log "VENV_DIR=%VENV_DIR%"

rem uv.lock's modified-time + size stand in for a hash: reading them costs
rem nothing (no subprocess), and any dependency change touches the file.
set "LOCK_STAMP="
for %%A in ("%LOCK_FILE%") do set "LOCK_STAMP=%%~tA|%%~zA"

rem ---------------- Fast path: reuse an existing venv ----------------
if not exist "%VENV_PYW%" goto :need_setup
if not exist "%MARKER%" goto :need_setup

set "STORED_STAMP="
for /f "usebackq delims=" %%L in ("%MARKER%") do set "STORED_STAMP=%%L"
if not "!STORED_STAMP!"=="!LOCK_STAMP!" (
    call :log "uv.lock changed since the marker was written -- re-syncing."
    goto :need_setup
)

call :log "Fast path OK (marker matches, pythonw present) -- launching without reinstalling."
goto :launch

rem ---------------- Slow path: build the environment ----------------
:need_setup
echo Setting up COG. This takes a few minutes and only happens once.
echo Please wait - do not close this window.
echo.
call :log "Fast path unavailable -- starting setup."

set "UV_CMD="
set "PYEXE="

where uv >nul 2>&1
if errorlevel 1 goto :try_py_launcher
set "UV_CMD=uv"
call :log "Found uv on PATH."
goto :have_uv

:try_py_launcher
call :log "uv not on PATH -- trying the 'py' launcher (py -3.12)."
py -3.12 -c "import sys" >nul 2>&1
if errorlevel 1 goto :try_python_onpath
set "PYEXE=py -3.12"
call :log "Found Python via: py -3.12"
goto :have_pyexe

:try_python_onpath
call :log "py -3.12 not available -- trying 'python' on PATH."
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 goto :try_install_uv
set "PYEXE=python"
call :log "Found Python 3.12+ via: python"
goto :have_pyexe

:try_install_uv
call :log "No local Python 3.12+ found -- attempting to install uv for this user."
echo   No compatible Python installation was found on this computer.
echo   Installing a small setup tool (uv) to your user profile - this
echo   needs no administrator rights...
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { irm https://astral.sh/uv/install.ps1 | iex } catch { exit 1 }" >>"%LOG%" 2>&1
if errorlevel 1 goto :uv_install_failed
set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where uv >nul 2>&1
if errorlevel 1 goto :uv_install_failed
set "UV_CMD=uv"
call :log "uv installed successfully."
goto :have_uv

:uv_install_failed
call :fail "Python 3.12 or newer could not be found on this computer, and the automatic setup tool (uv) could not be installed. This is usually either a blocked internet connection or a company security policy preventing the download. Please contact IT for help installing Python 3.12+, or send the log below to Omar."

rem ---------------- Install via uv (preferred: exact pinned versions) ----------------
:have_uv
call :check_internet
if "!NET_OK!"=="0" call :fail "No internet connection could be reached (tried pypi.org). COG needs internet the first time it runs, to download its Python packages. If you are on a work network, this may be blocked by a proxy or firewall - please contact IT."

call :log "Installing with uv sync (this is the long step -- please wait)."
set "UV_PROJECT_ENVIRONMENT=%VENV_DIR%"
uv sync --python 3.12 >>"%LOG%" 2>&1
if errorlevel 1 goto :install_failed
goto :verify_install

rem ---------------- Install via venv + pip (fallback) ----------------
:have_pyexe
call :check_internet
if "!NET_OK!"=="0" call :fail "No internet connection could be reached (tried pypi.org). COG needs internet the first time it runs, to download its Python packages. If you are on a work network, this may be blocked by a proxy or firewall - please contact IT."

call :log "Creating virtual environment with: %PYEXE%"
%PYEXE% -m venv "%VENV_DIR%" >>"%LOG%" 2>&1
if errorlevel 1 goto :install_failed

call :log "Installing dependencies with pip (this is the long step -- please wait)."
"%VENV_PY%" -m pip install --upgrade pip >>"%LOG%" 2>&1
"%VENV_PY%" -m pip install -r "%REPO_DIR%requirements.txt" >>"%LOG%" 2>&1
if errorlevel 1 goto :install_failed
goto :verify_install

:install_failed
call :fail "Setup failed while installing COG's Python packages. This can happen if the internet connection dropped partway through, or if antivirus software blocked part of the install. Please try running Start COG.bat again; if it keeps failing, send the log below to Omar."

rem ---------------- Verify + launch ----------------
:verify_install
call :log "Verifying the new environment can import cog."
"%VENV_PY%" -c "import cog" >>"%LOG%" 2>&1
if errorlevel 1 goto :verify_failed
> "%MARKER%" echo !LOCK_STAMP!
call :log "Setup complete."
goto :launch

:verify_failed
call :fail "Setup finished but COG still could not be started. Please send the log below to Omar."

:launch
echo Starting COG...
rem `start` still allocates a blank, visible pseudo-console window for the
rem child on Windows 11 (Windows Terminal owns the console subsystem, and
rem this happens even though pythonw.exe is a GUI-subsystem binary). Handing
rem the launch to WScript.Shell.Run instead never touches the console
rem subsystem at all, so no window -- blank or otherwise -- is created.
call :log "Launching COG GUI via WScript (pythonw, fully detached, no console)."
> "%LAUNCH_VBS%" echo Set objShell = CreateObject("WScript.Shell")
>>"%LAUNCH_VBS%" echo objShell.Run """%VENV_PYW%"" ""%REPO_DIR%main.py""", 0, False
wscript.exe //nologo "%LAUNCH_VBS%"
exit /b 0

rem ============================================================
rem  Subroutines (only ever reached via "call", never fallen into)
rem ============================================================

:log
>>"%LOG%" echo [%date% %time%] %~1
exit /b 0

:check_internet
set "NET_OK=0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Invoke-WebRequest -Uri 'https://pypi.org' -UseBasicParsing -TimeoutSec 8 | Out-Null; exit 0 } catch { exit 1 }" >>"%LOG%" 2>&1
if not errorlevel 1 set "NET_OK=1"
exit /b 0

:fail
echo.
echo ============================================================
echo   COG could not start
echo ============================================================
echo   %~1
echo.
echo   A detailed log was saved to:
echo   %LOG%
echo.
echo   Please send that file to Omar if you need help.
echo ============================================================
echo.
call :log "FAILURE: %~1"
pause
exit 1
