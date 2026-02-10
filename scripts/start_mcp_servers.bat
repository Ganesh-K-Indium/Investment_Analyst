@echo off
setlocal EnableDelayedExpansion

REM =============================================================================
REM MCP Servers Startup Script for Investment Analyst API (Windows)
REM =============================================================================

REM Configuration
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..
set VENV_PATH=%PROJECT_ROOT%\venv
set QUANT_DIR=%PROJECT_ROOT%\quant
set LOG_DIR=%PROJECT_ROOT%\logs\mcp

REM Create log directory if it doesn't exist
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM Server Configurations
REM 1. Stock Info
set SERVER1_NAME=stock_info
set SERVER1_DIR=yahoo-finance-mcp
set SERVER1_SCRIPT=server.py
set SERVER1_PORT=8565

REM 2. Technical Analysis
set SERVER2_NAME=technical
set SERVER2_DIR=Stock_Analysis
set SERVER2_SCRIPT=server_mcp.py
set SERVER2_PORT=8566

REM 3. Research
set SERVER3_NAME=research
set SERVER3_DIR=research_mcp
set SERVER3_SCRIPT=server_mcp.py
set SERVER3_PORT=8567

REM Process arguments
set COMMAND=%1
if "%COMMAND%"=="" set COMMAND=start

if "%COMMAND%"=="start" goto :start_all
if "%COMMAND%"=="stop" goto :stop_all
if "%COMMAND%"=="restart" goto :restart_all
if "%COMMAND%"=="status" goto :show_status
goto :help

:check_venv
if not exist "%VENV_PATH%" (
    echo [ERROR] Virtual environment not found at %VENV_PATH%
    echo Please create it with: python -m venv venv
    exit /b 1
)
exit /b 0

:activate_venv
call "%VENV_PATH%\Scripts\activate.bat"
exit /b 0

:start_server
set NAME=%1
set DIR=%2
set SCRIPT=%3
set PORT=%4

echo Starting %NAME% server...

REM Check if port is in use
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [WARNING] %NAME% server already running on port %PORT%
    exit /b 0
)

set SERVER_DIR=%QUANT_DIR%\%DIR%
set SERVER_SCRIPT=%SERVER_DIR%\%SCRIPT%
set LOG_FILE=%LOG_DIR%\%NAME%.log

if not exist "%SERVER_DIR%" (
    echo [ERROR] Server directory not found: %SERVER_DIR%
    exit /b 1
)

if not exist "%SERVER_SCRIPT%" (
    echo [ERROR] Server script not found: %SERVER_SCRIPT%
    exit /b 1
)

pushd "%SERVER_DIR%"
REM Start in background using start /B
REM Redirecting output in batch is tricky with start /B, so we use a wrapper or just direct python call if blocking (but we want non-blocking)
REM Powershell start-process might be cleaner, but keeping it batch for now.
REM Using standard redirect > log 2>&1 works with start /B for the command being started.
start /B "MCP_%NAME%" python "%SCRIPT%" > "%LOG_FILE%" 2>&1
popd

REM Give it a moment
timeout /t 2 /nobreak >nul

REM Check if it's listening now
netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [SUCCESS] %NAME% server started on port %PORT%
) else (
    echo [ERROR] %NAME% server failed to start. Check logs at %LOG_FILE%
)
exit /b 0

:stop_server
set NAME=%1
set PORT=%2

echo Stopping %NAME% server...

REM Find PID by port
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do set PID=%%a

if defined PID (
    taskkill /F /PID %PID% >nul 2>&1
    echo [SUCCESS] %NAME% server stopped (PID: %PID%)
    set PID=
) else (
    echo [WARNING] %NAME% server not running (Port %PORT% free)
)
exit /b 0

:start_all
call :check_venv || exit /b 1
call :activate_venv

echo.
echo ===========================================================================
echo Starting MCP Servers
echo ===========================================================================
echo.

call :start_server %SERVER1_NAME% %SERVER1_DIR% %SERVER1_SCRIPT% %SERVER1_PORT%
call :start_server %SERVER2_NAME% %SERVER2_DIR% %SERVER2_SCRIPT% %SERVER2_PORT%
call :start_server %SERVER3_NAME% %SERVER3_DIR% %SERVER3_SCRIPT% %SERVER3_PORT%

echo.
echo All start attempts completed. Check status above.
goto :eof

:stop_all
echo.
echo ===========================================================================
echo Stopping MCP Servers
echo ===========================================================================
echo.

call :stop_server %SERVER1_NAME% %SERVER1_PORT%
call :stop_server %SERVER2_NAME% %SERVER2_PORT%
call :stop_server %SERVER3_NAME% %SERVER3_PORT%
goto :eof

:restart_all
call :stop_all
timeout /t 2 /nobreak >nul
call :start_all
goto :eof

:show_status
echo.
echo ===========================================================================
echo MCP Servers Status
echo ===========================================================================
echo.

call :check_status %SERVER1_NAME% %SERVER1_PORT%
call :check_status %SERVER2_NAME% %SERVER2_PORT%
call :check_status %SERVER3_NAME% %SERVER3_PORT%
goto :eof

:check_status
set NAME=%1
set PORT=%2

netstat -ano | findstr ":%PORT%" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [RUNNING] %NAME% server (Port: %PORT%)
) else (
    echo [STOPPED] %NAME% server (Port: %PORT%)
)
exit /b 0

:help
echo.
echo Usage: start_mcp_servers.bat [command]
echo.
echo Commands:
echo   start      Start all MCP servers (default)
echo   stop       Stop all MCP servers
echo   restart    Restart all MCP servers
echo   status     Check status of all servers
echo.
goto :eof
