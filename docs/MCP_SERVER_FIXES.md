# MCP Server Issues and Fixes

## Technical Analysis Server (Port 8566) - ImportError

### Issue
The Technical Analysis MCP server fails to start with:
```
ImportError: cannot import name 'genai' from 'google' (unknown location)
```

### Root Cause
Line 19 in `/quant/Stock_Analysis/server_mcp.py` has incorrect import:
```python
from google import genai  # INCORRECT
from google.genai import types  # INCORRECT
```

### Fix Required
Update the imports in `/quant/Stock_Analysis/server_mcp.py`:

```python
# OLD (lines 19-20):
from google import genai
from google.genai import types

# NEW:
import google.generativeai as genai
# types can be accessed via genai.types
```

Then ensure `google-generativeai` is installed:
```bash
pip install google-generativeai
```

### Temporary Workaround
The main API now gracefully handles missing MCP servers:
- ✅ Stock Information Agent works (port 8565)
- ❌ Technical Analysis Agent unavailable (port 8566 - needs fix above)
- ✅ Research Agent works (port 8567)
- ✅ Ticker Finder Agent works (no MCP server needed)

The supervisor will work with 3/4 agents and inform users if technical analysis is requested but unavailable.

### Testing After Fix
1. Fix the import in `quant/Stock_Analysis/server_mcp.py`
2. Restart the MCP servers:
   ```bash
   ./scripts/start_mcp_servers.sh restart
   ```
3. Restart the main API:
   ```bash
   python -m uvicorn app.main:app --reload
   ```

## Current Status
- **Main API**: ✅ Runs successfully without stock analysis
- **Stock Info Server**: ✅ Running (8565)
- **Technical Server**: ❌ Import error (8566) - FIX NEEDED
- **Research Server**: ✅ Running (8567)
- **Supervisor**: ✅ Initializes with 3/4 agents

The system is designed to degrade gracefully, so users can still:
- Use document analysis (RAG)
- Get stock information (prices, financials)
- Get research and ratings
- Find ticker symbols
- Cannot get technical analysis charts until port 8566 is fixed
