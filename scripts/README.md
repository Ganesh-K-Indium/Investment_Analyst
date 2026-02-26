# Startup Scripts for Investment Analyst API

This directory contains scripts to manage the Investment Analyst API and its services.

## 📁 Available Scripts

### 1. `start_mcp_servers.sh` - MCP Servers Manager
**Purpose**: Start/stop/manage the 3 MCP servers required for stock analysis

```bash
# Start all MCP servers
./scripts/start_mcp_servers.sh start

# Stop all servers
./scripts/start_mcp_servers.sh stop

# Check status
./scripts/start_mcp_servers.sh status

# Restart all servers
./scripts/start_mcp_servers.sh restart

# View logs
./scripts/start_mcp_servers.sh logs
```

**MCP Servers:**
- **Stock Information** (port 8565) - Yahoo Finance data
- **Technical Analysis** (port 8566) - Charts and indicators
- **Research** (port 8567) - Analyst ratings and news

### 2. `start_api.sh` - Main API Server
**Purpose**: Start the Investment Analyst API server

```bash
# Start API only (MCP servers optional)
./scripts/start_api.sh

# Start MCP servers + API
./scripts/start_api.sh --with-mcp

# Development mode with auto-reload
./scripts/start_api.sh --dev

# Full development environment
./scripts/start_api.sh --with-mcp --dev
```

### 3. `start_server.sh` - Legacy Script
Simple server startup (kept for backwards compatibility)

```bash
./scripts/start_server.sh
```


## 🪟 Windows Usage

Equivalent batch scripts are available for Windows users.

### 1. `start_mcp_servers.bat`
**Usage**:
```cmd
REM Start all servers
.\scripts\start_mcp_servers.bat start

REM Stop all servers
.\scripts\start_mcp_servers.bat stop

REM Check status
.\scripts\start_mcp_servers.bat status
```

### 2. `start_api.bat`
**Usage**:
```cmd
REM Start API only
.\scripts\start_api.bat

REM Start API + MCP Servers
.\scripts\start_api.bat --with-mcp

REM Development mode
.\scripts\start_api.bat --dev
```

## 🚀 Quick Start Guide

### Option 1: Full System (Recommended)

Start everything with one command:

```bash
# Start MCP servers and API
./scripts/start_api.sh --with-mcp
```

### Option 2: Manual Control

Start services separately:

```bash
# Terminal 1: Start MCP servers
./scripts/start_mcp_servers.sh start

# Terminal 2: Start API (in dev mode)
./scripts/start_api.sh --dev
```

### Option 3: API Only

Run without stock analysis features:

```bash
# Just the API (document analysis only)
./scripts/start_api.sh
```

## 📊 Service Architecture

```
┌─────────────────────────────────────────────────┐
│         Investment Analyst API (8000)           │
│  ┌──────────────────────────────────────────┐  │
│  │  Document Analysis  │  Stock Analysis    │  │
│  │  (Always Available) │  (Needs MCP)       │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
                      │
        ┌─────────────┴──────────────┐
        │                            │
   ┌────▼────┐   ┌────▼────┐  ┌────▼────┐
   │ Stock   │   │Technical│  │Research │
   │ Info    │   │Analysis │  │ Agent   │
   │ (8565)  │   │ (8566)  │  │ (8567)  │
   └─────────┘   └─────────┘  └─────────┘
```

## 📝 Configuration

### Environment Variables

Required in `.env`:
```bash
OPENAI_API_KEY=your_key
GROQ_API_KEY=your_key (optional)
TAVILY_API_KEY=your_key
QDRANT_URL=http://localhost:6333
```

### Log Files

Logs are stored in:
- **MCP Logs**: `logs/mcp/*.log`
- **PID Files**: `logs/mcp/pids/*.pid`
- **API Logs**: Console output

## 🔧 Troubleshooting

### API Continuously Restarting in Dev Mode

If you see messages like `WatchFiles detected changes` and the server keeps reloading, this is likely due to file watcher detecting changes in `venv/`. This is fixed by the startup scripts which automatically exclude:
- `venv/*` - Virtual environment packages
- `*.pyc` - Compiled Python files
- `__pycache__/*` - Python cache directories

If using uvicorn directly, specify only the directories to watch:
```bash
python -m uvicorn app.main:app --reload --reload-dir app --reload-dir rag --reload-dir quant --reload-dir schemas --reload-dir ingestion
```

This approach explicitly watches only your source code directories and completely avoids watching `venv/`.

### MCP Servers Won't Start

```bash
# Check what's on the ports
lsof -i :8565
lsof -i :8566
lsof -i :8567

# Stop any existing processes
./scripts/start_mcp_servers.sh stop

# Try starting again
./scripts/start_mcp_servers.sh start
```

### API Port Already in Use

```bash
# Find process on port 8000
lsof -i :8000

# Kill it
kill $(lsof -ti:8000)

# Or use a different port
python -m uvicorn app.main:app --port 8001
```

### Dependencies Missing

```bash
# Activate venv and install
source venv/bin/activate
pip install -r requirements.txt
```

### MCP Server Logs

```bash
# View all logs
./scripts/start_mcp_servers.sh logs

# Or view specific log
tail -f logs/mcp/stock_info.log
```

## 🧪 Testing

After starting services:

```bash
# Test main API
curl http://localhost:8000/health

# Test MCP servers
curl http://localhost:8565/health  # Stock Info
curl http://localhost:8566/health  # Technical
curl http://localhost:8567/health  # Research

# Test stock analysis
curl http://localhost:8000/quant/health
```

## 💡 Development Tips

### Auto-reload During Development

```bash
# API with auto-reload
./scripts/start_api.sh --dev

# MCP servers need manual restart
./scripts/start_mcp_servers.sh restart
```

### Running in Background

```bash
# MCP servers automatically run in background
./scripts/start_mcp_servers.sh start

# API in background
nohup ./scripts/start_api.sh > api.log 2>&1 &
```

### Stopping Everything

```bash
# Stop MCP servers
./scripts/start_mcp_servers.sh stop

# Stop API
kill $(lsof -ti:8000)
```

## 📦 What Each Script Does

### start_mcp_servers.sh
- ✅ Checks virtual environment
- ✅ Creates log directories
- ✅ Starts 3 MCP servers in background
- ✅ Saves PIDs for management
- ✅ Provides status checking
- ✅ Handles graceful shutdown

### start_api.sh
- ✅ Checks dependencies
- ✅ Validates .env file
- ✅ Optionally starts MCP servers
- ✅ Shows MCP status
- ✅ Starts API with proper configuration
- ✅ Supports dev/prod modes

## 🎯 Common Workflows

### Daily Development

```bash
# Morning: Start full system
./scripts/start_api.sh --with-mcp --dev

# During day: API auto-reloads on code changes
# (MCP servers keep running)

# Evening: Stop everything
./scripts/start_mcp_servers.sh stop
# Ctrl+C to stop API
```

### Production Deployment

```bash
# 1. Start MCP servers
./scripts/start_mcp_servers.sh start

# 2. Start API (no reload)
./scripts/start_api.sh

# Or use systemd, supervisor, etc.
```

### Testing Without Stock Features

```bash
# Just start API
./scripts/start_api.sh

# Document analysis works
# Stock analysis returns 503 (expected)
```

## 📚 More Information

- **Main README**: `../README.md`
- **Integration Guide**: `../docs/QUANT_INTEGRATION.md`
- **API Docs**: http://localhost:8000/docs (after starting)

## 🔗 Quick Links

After starting the API:

- 🏠 **API Root**: http://localhost:8000
- 📚 **Documentation**: http://localhost:8000/docs
- 🏥 **Health Check**: http://localhost:8000/health
- 📈 **Stock System**: http://localhost:8000/quant/health
- 💼 **Portfolios**: http://localhost:8000/portfolios
- 💡 **Web UI**: Open `static/index.html` in browser

---

**Need help?** Check the main README or run `./scripts/start_api.sh --help`
