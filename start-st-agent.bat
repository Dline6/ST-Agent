@echo off
rem ======================================================================
rem  ST Agent - one-click launch & try-out (local loopback UI in browser)
rem
rem    start-st-agent.bat            default storage root %LOCALAPPDATA%\STAgent
rem    start-st-agent.bat dev        also serve the dev panel (walkthrough samples)
rem    start-st-agent.bat trial      use an isolated trial root, leaves real data alone
rem    start-st-agent.bat help       this text
rem
rem  Passphrase and LLM settings come from the repo-root .env
rem  (ST_AGENT_PASSPHRASE / LLM_API_KEY / LLM_BASE_URL / LLM_MODEL).
rem  Stop: press any key at the prompt below (or just close this window).
rem
rem  ASCII only on purpose: a mid-file "chcp" desyncs cmd's byte-offset
rem  reader on multibyte text and corrupts the rest of the file.
rem ======================================================================

setlocal EnableExtensions
cd /d "%~dp0"

set "DEV="
set "TRIAL="

:parse
if "%~1"=="" goto :parsed
if /i "%~1"=="dev"    set "DEV=--dev"
if /i "%~1"=="trial"  set "TRIAL=1"
if /i "%~1"=="help"   goto :usage
if /i "%~1"=="-h"     goto :usage
if /i "%~1"=="--help" goto :usage
shift
goto :parse
:parsed

rem -- 1. pick an interpreter: ST_AGENT_PYTHON, then .venv, then PATH ------
set "PY="
if defined ST_AGENT_PYTHON set "PY=%ST_AGENT_PYTHON%"
if not defined PY if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY set "PY=python"

"%PY%" --version >nul 2>&1
if errorlevel 1 (
  echo [x] No usable Python at: %PY%
  echo     Install Python 3.12+ or point ST_AGENT_PYTHON at your interpreter.
  goto :end
)

rem -- 2. this project is src-layout and not pip-installed: PYTHONPATH finds it
set "PYTHONPATH=%~dp0src"

"%PY%" -c "import st_agent, pydantic, cryptography" >nul 2>&1
if errorlevel 1 (
  echo [x] Dependencies are incomplete. Run once from the project root:
  echo         "%PY%" -m pip install -e .
  goto :end
)

rem -- 3. passphrase: environment wins, else the repo .env ------------------
if not defined ST_AGENT_PASSPHRASE if exist "%~dp0.env" (
  for /f "usebackq tokens=1,* delims==" %%A in ("%~dp0.env") do (
    if /i "%%~A"=="ST_AGENT_PASSPHRASE" if not "%%~B"=="" set "ST_AGENT_PASSPHRASE=%%~B"
  )
)
set "PPARG="
if defined ST_AGENT_PASSPHRASE set "PPARG=--passphrase-env ST_AGENT_PASSPHRASE"

rem -- 4. storage root + log sink ------------------------------------------
set "ROOTDIR=%LOCALAPPDATA%\STAgent"
if defined TRIAL set "ROOTDIR=%LOCALAPPDATA%\STAgent-trial"

set "LOG=%TEMP%\st-agent-launch.log"
del /q "%LOG%" >nul 2>&1

echo.
echo ST Agent launcher
echo   python : %PY%
echo   root   : %ROOTDIR%
if defined DEV echo   extra  : --dev (dev panel with walkthrough samples)
if defined ST_AGENT_PASSPHRASE (
  echo   secret : passphrase taken from %PPARG%
) else (
  echo   secret : no passphrase found - startup will ask for one
)
echo.
echo Starting backend ...

start "" /b cmd /c ""%PY%" -m st_agent --handshake json %DEV% --root "%ROOTDIR%" %PPARG% > "%LOG%" 2>&1"

rem -- 5. wait for the ready handshake (about one second per tick) --------
set /a TICKS=0
:wait
set /a TICKS+=1
if %TICKS% GTR 60 goto :badnews
findstr /c:"\"event\": \"ready\"" "%LOG%" >nul 2>&1 && goto :ready
findstr /c:"\"event\": \"error\"" "%LOG%" >nul 2>&1 && goto :badnews
ping -n 2 127.0.0.1 >nul
goto :wait

:ready
set "LINE="
for /f "usebackq delims=" %%L in (`findstr /c:"\"event\": \"ready\"" "%LOG%"`) do set "LINE=%%L"
set "PLAIN=%LINE:"=%"
rem  Key order in the handshake is fixed (event, host, port, token, pid, root,
rem  version) and no value before "root" contains a space or a comma, so the
rem  6th / 8th / 10th comma-or-space separated fields are port / token / pid.
for /f "tokens=6,8,10 delims=, " %%P in ("%PLAIN%") do (
  set "PORT=%%P"
  set "TOK=%%Q"
  set "PID=%%R"
)
if "%PORT%"=="" goto :badnews

echo.
echo   URL    : http://127.0.0.1:%PORT%/#t=%TOK%
echo   Open it with 127.0.0.1 (NOT localhost - the request guard rejects that).
echo   Opening the default browser ...
start "" "http://127.0.0.1:%PORT%/#t=%TOK%"
echo.
echo Backend is running. Press any key to stop it and exit.
pause >nul
taskkill /f /pid %PID% >nul 2>&1
echo Backend stopped.
goto :end

:badnews
echo.
echo [x] Backend never reported "ready". Its output was:
echo ----------------------------------------------------------------------
type "%LOG%"
echo ----------------------------------------------------------------------
echo Likely causes:
echo   * passphrase mismatch - .env ST_AGENT_PASSPHRASE differs from the one
echo     this storage root was originally created with;
echo   * the root is corrupt, or the port could not be bound.
echo Try a clean isolated root:   start-st-agent.bat trial
goto :end

:end
echo.
pause
endlocal
exit /b 0

:usage
echo ST Agent - launch ^& try-out
echo.
echo   start-st-agent.bat            default root %LOCALAPPDATA%\STAgent
echo   start-st-agent.bat dev        also serve the dev panel
echo   start-st-agent.bat trial      isolated trial root %LOCALAPPDATA%\STAgent-trial
echo   start-st-agent.bat help       this text
echo.
echo Passphrase and LLM settings come from the repo-root .env.
echo ST_AGENT_PYTHON overrides the interpreter (beats .venv and PATH).
echo.
echo First "trial" run: any passphrase works - the root is created with it,
echo and later runs must reuse the same one.
echo Pages that are not built yet show an availability probe placeholder;
echo that is an honest six-state render, not a crash.
goto :end
