@echo off
REM Kept for existing habits: everything now runs through the cross-platform tool, scripts\dev.py.
REM   scripts\start_api.bat         ==  py scripts\dev.py start          (services, MCP servers, workers, API, UI)
REM   scripts\start_api.bat --dev   ==  py scripts\dev.py start --reload
setlocal enabledelayedexpansion
cd /d "%~dp0.."
set "ARGS="
for %%A in (%*) do (
    if /I "%%~A"=="--dev" (set "ARGS=!ARGS! --reload") else (
    if /I "%%~A"=="--with-mcp" (call ) else (
    if /I "%%~A"=="--with-workers" (call ) else (
    if /I "%%~A"=="--all" (call ) else (
    if /I "%%~A"=="--api-only" (set "ARGS=!ARGS! --no-mcp --no-workers --no-ui") else (
    set "ARGS=!ARGS! %%~A")))))
)
where py >nul 2>nul && (py scripts\dev.py start !ARGS! & exit /b) 
python scripts\dev.py start !ARGS!
