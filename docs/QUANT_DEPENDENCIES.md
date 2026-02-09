# Quant Integration - Dependencies and Setup

## Overview
This document lists all dependencies required for the Quant Stock Analysis integration into the Investment Analyst API.

## Critical Dependencies

### LangGraph Supervisor
The quant system uses a supervisor pattern to orchestrate multiple specialized agents.

```bash
pip install langgraph-supervisor==0.0.31
```

### LangChain MCP Adapters
Required for connecting to MCP (Model Context Protocol) servers that provide stock data.

```bash
pip install langchain-mcp-adapters==0.2.1
```

### FastAPI Update
The MCP adapters require a newer version of Starlette, which requires FastAPI 0.128+.

```bash
pip install --upgrade fastapi>=0.128.5
```

## Complete Installation

### Option 1: Install from requirements file
```bash
source venv/bin/activate
pip install -r requirements.txt
```

### Option 2: Install individually
```bash
source venv/bin/activate

# Core quant dependencies
pip install langgraph-supervisor==0.0.31
pip install langchain-mcp-adapters==0.2.1

# Upgrade FastAPI for compatibility
pip install --upgrade fastapi>=0.128.5

# Ensure LangGraph packages are installed
pip install langgraph>=1.0.4
pip install langgraph-checkpoint-sqlite>=3.0.1
```

## Dependency Conflicts Resolved

### Starlette Version Conflict
**Issue**: `langchain-mcp-adapters` requires `starlette>=0.52.1`, but older FastAPI versions require `starlette<0.51.0`.

**Solution**: Upgrade FastAPI to 0.128.5 or higher, which supports `starlette>=0.52.1`.

```bash
pip install --upgrade fastapi
```

## Verification

Test that all imports work correctly:

```bash
source venv/bin/activate
python3 << 'EOF'
import sys
import os
sys.path.insert(0, os.path.join(os.getcwd(), 'quant', 'stock_agent'))

# Test imports
from langgraph_supervisor import create_supervisor
from stock_exchange_agent.subagents.stock_information.langgraph_agent import create_stock_information_agent
from stock_exchange_agent.subagents.technical_analysis_agent.langgraph_agent import create_technical_analysis_agent
from stock_exchange_agent.subagents.ticker_finder_tool.langgraph_agent import create_ticker_finder_agent
from stock_exchange_agent.subagents.research_agent.langgraph_agent import create_research_agent

print("✅ All quant imports successful!")
EOF
```

## MCP Servers (Optional)

For full functionality, the quant system connects to MCP servers:

1. **Stock Information Server** - Port 8565
2. **Technical Analysis Server** - Port 8566
3. **Research Server** - Port 8567

**Note**: The system will work without these servers but with limited functionality. The startup will show warnings if servers are not available.

To start MCP servers (if available):
```bash
cd quant/stock_agent
# Follow server-specific startup instructions
```

## Environment Variables

```bash
# Optional: Separate database for stock agent memory
STOCK_SQLITE_DB_PATH=sqlite:///checkpoints_stock.db

# By default, uses shared checkpoints.sqlite
```

## Common Issues

### Import Error: No module named 'langgraph_supervisor'
**Solution**: 
```bash
pip install langgraph-supervisor
```

### Import Error: No module named 'langchain_mcp_adapters'
**Solution**: 
```bash
pip install langchain-mcp-adapters
```

### Dependency conflict with starlette
**Solution**: 
```bash
pip install --upgrade fastapi
```

### Module not found errors for stock_exchange_agent
**Cause**: Python path not set correctly

**Solution**: The `app/services/stock_agent.py` automatically adds the correct path:
```python
quant_dir = os.path.join(os.getcwd(), 'quant', 'stock_agent')
sys.path.insert(0, quant_dir)
```

Make sure you're running from the project root directory.

## Package Versions (Verified Working)

```
fastapi==0.128.5
starlette==0.52.1
langgraph==1.0.4
langgraph-checkpoint==3.0.1
langgraph-checkpoint-sqlite==3.0.1
langgraph-prebuilt==1.0.5
langgraph-sdk==0.2.6
langgraph-supervisor==0.0.31
langchain-core==1.1.3
langchain-openai==0.3.14
langchain-mcp-adapters==0.2.1
mcp==1.26.0
```

## Next Steps

After installing dependencies:

1. **Test the server**:
   ```bash
   source venv/bin/activate
   python app/main.py
   ```

2. **Check health**:
   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/quant/health
   ```

3. **Test a query**:
   ```bash
   curl -X POST http://localhost:8000/quant/query \
     -H "Content-Type: application/json" \
     -d '{
       "query": "What is the current price of Apple?",
       "user_id": "test_user"
     }'
   ```

## Support

If you encounter import issues:

1. Verify you're using the virtual environment
2. Check Python path includes the project root
3. Verify all packages are installed: `pip list | grep -E "(langgraph|langchain)"`
4. Check for version conflicts: `pip check`

For more details, see:
- [QUANT_INTEGRATION.md](./QUANT_INTEGRATION.md) - API usage and endpoints
- [API_DOCUMENTATION.md](./API_DOCUMENTATION.md) - Complete API reference
