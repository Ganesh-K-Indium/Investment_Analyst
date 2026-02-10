@echo off
setlocal EnableDelayedExpansion

REM =============================================================================
REM Investment Analyst API Startup Script (Windows)
REM =============================================================================

REM Configuration
set SCRIPT_DIR=%~dp0
set PROJECT_ROOT=%SCRIPT_DIR%..
set VENV_PATH=%PROJECT_ROOT%\venv
set API_PORT=8000

REM Parse arguments
set WITH_MCP=false
set DEV_MODE=false

:parse_args
if "%~1"=="" goto :args_parsed
if "%~1"=="--with-mcp" (
    set WITH_MCP=true
    shift
    goto :parse_args
)
if "%~1"=="--dev" (
    set DEV_MODE=true
    shift
    goto :parse_args
)
if "%~1"=="--help" goto :show_help
if "%~1"=="-h" goto :show_help
shift
goto :parse_args

:args_parsed

REM Check/Create Virtual Environment
if not exist "%VENV_PATH%" (
    echo [INFO] Virtual environment not found at %VENV_PATH%
    echo [INFO] Creating virtual environment...
    python -m venv "%VENV_PATH%"
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to create virtual environment.
        exit /b 1
    )
    echo [SUCCESS] Virtual environment created.
)

REM Activate Virtual Environment
call "%VENV_PATH%\Scripts\activate.bat"
if %errorlevel% neq 0 (
    echo [ERROR] Failed to activate virtual environment.
    exit /b 1
)

REM Check Dependencies
echo [INFO] Checking dependencies...
python -c "import fastapi" >nul 2>&1
if %errorlevel% neq 0 (
    echo [WARNING] Dependencies not installed. Installing...
    pip install -r "%PROJECT_ROOT%\requirements.txt"
    if %errorlevel% neq 0 (
        echo [ERROR] Failed to install dependencies.
        exit /b 1
    )
    echo [SUCCESS] Dependencies installed.
) else (
    echo [SUCCESS] Dependencies check passed.
)

REM Check .env file
if not exist "%PROJECT_ROOT%\.env" (
    echo [WARNING] .env file not found
    if exist "%PROJECT_ROOT%\.env.example" (
        echo [INFO] Creating .env from .env.example...
        copy "%PROJECT_ROOT%\.env.example" "%PROJECT_ROOT%\.env" >nul
        echo [WARNING] Please edit .env file with your API keys before starting the server.
        echo [INFO] Exiting to allow configuration.
        exit /b 1
    ) else (
        echo [ERROR] No .env or .env.example file found.
        exit /b 1
    )
)

REM Start MCP Servers if requested
if "%WITH_MCP%"=="true" (
    echo.
    echo ===========================================================================
    echo Starting MCP Servers
    echo ===========================================================================
    echo.
    
    if exist "%SCRIPT_DIR%\start_mcp_servers.bat" (
        call "%SCRIPT_DIR%\start_mcp_servers.bat" start
    ) else (
        echo [WARNING] MCP startup script not found at %SCRIPT_DIR%\start_mcp_servers.bat
        echo [INFO] MCP servers will need to be started manually.
    )
    
    timeout /t 2 /nobreak >nul
)

REM Check if API port is in use
netstat -ano | findstr ":%API_PORT%" | findstr "LISTENING" >nul
if %errorlevel%==0 (
    echo [ERROR] Port %API_PORT% is already in use.
    echo [INFO] Stop the existing server or use a different port.
    exit /b 1
)

REM Start API Server
echo.
echo ===========================================================================
echo Starting Investment Analyst API
echo ===========================================================================
echo.
echo [SUCCESS] Starting server on http://localhost:%API_PORT%
echo.
echo [INFO] API Documentation: http://localhost:%API_PORT%/docs
echo [INFO] Health Check: http://localhost:%API_PORT%/health
echo [INFO] Web UI: Open static/index.html in browser
echo.

pushd "%PROJECT_ROOT%"
if "%DEV_MODE%"=="true" (
    echo [INFO] Development mode: Auto-reload enabled
    python -m uvicorn app.main:app --reload --port %API_PORT% --host 0.0.0.0
) else (
    echo [INFO] Production mode
    python -m uvicorn app.main:app --port %API_PORT% --host 0.0.0.0
)
popd

goto :eof

:show_help
echo.
echo Investment Analyst API Startup Script (Windows)
echo.
echo Usage:
echo   start_api.bat [options]
echo.
echo Options:
echo   --with-mcp    Start MCP servers before starting API
echo   --dev         Development mode (auto-reload on code changes)
echo   --help        Show this help message
echo.
goto :eof
