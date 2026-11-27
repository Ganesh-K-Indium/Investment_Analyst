@echo off
REM Runs the five MCP servers through the cross-platform tool (Ctrl+C stops them):  py scripts\dev.py mcp
REM To run the whole stack instead:  py scripts\dev.py start
cd /d "%~dp0.."
where py >nul 2>nul && (py scripts\dev.py mcp & exit /b)
python scripts\dev.py mcp
