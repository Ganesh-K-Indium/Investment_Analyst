# Investment Analyst API - Quant Stock Analysis Integration

## Overview

The Quant Stock Analysis system has been successfully integrated into the Investment Analyst API. This unified platform now provides comprehensive investment analysis capabilities combining document intelligence, real-time market data, and portfolio management.

## Architecture

### System Components

```
Investment Analyst API v2.1
├── Document Analysis (RAG System)
│   ├── Document Q&A from financial reports
│   └── Company comparison analysis
├── Portfolio Management
│   ├── Portfolio creation and management
│   └── Session tracking
├── Data Integrations
│   ├── AWS S3, Azure Blob, Google Drive
│   └── SharePoint, SFTP
└── Stock Market Analysis (Quant System)
    ├── Stock Supervisor Agent
    └── Sub-Agents
        ├── Stock Information Agent (port 8565)
        ├── Technical Analysis Agent (port 8566)
        ├── Research Agent (port 8567)
        └── Ticker Finder Agent
```

### Key Integration Points

1. **Shared Infrastructure**
   - Same FastAPI app instance
   - Shared SQLite checkpointer for conversation memory
   - Common database for portfolio management
   - Unified health monitoring

2. **Modular Design**
   - Separate router: `app/api/quant.py`
   - Separate service: `app/services/stock_agent.py`
   - Independent from RAG system
   - Can run even if MCP servers are unavailable (with limited functionality)

3. **Portfolio Integration**
   - Stock queries can be linked to portfolios
   - Session tracking for conversation continuity
   - User-based query history

## API Endpoints

### Base URL
All quant endpoints are prefixed with `/quant`

### 1. Query Stock Agent

**POST** `/quant/query`

Send a query to the stock analysis supervisor agent.

#### Request Body
```json
{
  "query": "What is Apple's current stock price?",
  "user_id": "user123",
  "portfolio_id": 1,  // Optional
  "session_id": "session_xyz"  // Optional, auto-generated if not provided
}
```

#### Response
```json
{
  "response": "Apple (AAPL) is currently trading at $178.45...",
  "session_id": "quant_portfolio_1_20260206_143022",
  "portfolio_id": 1,
  "timestamp": "2026-02-06T14:30:22.123456",
  "success": true,
  "agent_used": "stock_information_agent",
  "metadata": {
    "message_count": 15,
    "new_messages": 3
  }
}
```

#### Capabilities
- Stock prices and market data
- Financial statements and company fundamentals
- Technical analysis (RSI, SMA, MACD, Bollinger Bands)
- Analyst ratings and research
- News and sentiment analysis
- Bull/bear scenarios
- Ticker symbol lookup from company names

### 2. Health Check

**GET** `/quant/health`

Check the status of stock analysis system and MCP servers.

#### Response
```json
{
  "status": "healthy",
  "servers_ready": {
    "stock_information": true,
    "technical_analysis": true,
    "research": true
  },
  "agents_ready": true,
  "timestamp": "2026-02-06T14:30:22.123456"
}
```

### 3. Get Capabilities

**GET** `/quant/capabilities`

List all available stock analysis capabilities.

#### Response
```json
{
  "fundamental_analysis": [
    "Current stock prices and market data",
    "Financial statements",
    ...
  ],
  "technical_analysis": [
    "Simple Moving Average (SMA)",
    "RSI, MACD, Bollinger Bands",
    ...
  ],
  "research_analysis": [
    "Analyst ratings",
    "Sentiment analysis",
    ...
  ],
  ...
}
```

### 4. Get Session History

**GET** `/quant/sessions/{session_id}`

Retrieve conversation history for a stock analysis session.

#### Response
```json
{
  "session_id": "quant_user123_20260206_143022",
  "message_count": 15,
  "messages": [
    {
      "type": "human",
      "content": "What is Apple's stock price?",
      "name": null,
      "id": "msg_001"
    },
    {
      "type": "ai",
      "content": "Apple (AAPL) is currently...",
      "name": "stock_information_agent",
      "id": "msg_002"
    }
  ]
}
```

### 5. Get Portfolio Stock Sessions

**GET** `/quant/portfolio/{portfolio_id}/sessions`

Get all stock analysis sessions linked to a portfolio.

## Usage Examples

### Example 1: Basic Stock Query

```python
import requests

response = requests.post(
    "http://localhost:8000/quant/query",
    json={
        "query": "What is Tesla's current stock price and PE ratio?",
        "user_id": "user123"
    }
)

print(response.json()["response"])
```

### Example 2: Portfolio-Linked Query

```python
# First create a portfolio
portfolio = requests.post(
    "http://localhost:8000/portfolios/",
    json={
        "user_id": "user123",
        "name": "Tech Portfolio",
        "company_names": ["Apple", "Microsoft", "Google"]
    }
).json()

# Then query with portfolio context
response = requests.post(
    "http://localhost:8000/quant/query",
    json={
        "query": "Show me the RSI chart for Apple over the last 6 months",
        "user_id": "user123",
        "portfolio_id": portfolio["id"]
    }
)
```

### Example 3: Multi-Turn Conversation

```python
# First query
response1 = requests.post(
    "http://localhost:8000/quant/query",
    json={
        "query": "Find the ticker for Tesla",
        "user_id": "user123"
    }
)

session_id = response1.json()["session_id"]

# Follow-up query (agent remembers Tesla ticker)
response2 = requests.post(
    "http://localhost:8000/quant/query",
    json={
        "query": "Now show me its technical analysis",
        "user_id": "user123",
        "session_id": session_id
    }
)
```

## MCP Servers

The stock analysis system requires 3 MCP servers to be running for full functionality:

1. **Stock Information Server** (port 8565)
   - Provides fundamental data, prices, financials, news

2. **Technical Analysis Server** (port 8566)
   - Generates technical charts and indicators

3. **Research Server** (port 8567)
   - Provides analyst ratings, research, sentiment

### Starting MCP Servers

The system will attempt to connect to these servers during startup. If they're not available:
- The system will initialize with limited functionality
- A warning will be displayed in the logs
- The API will still accept requests but some features may not work

To start the MCP servers (if available):
```bash
# Check the quant/stock_agent directory for server startup scripts
cd quant/stock_agent
# Follow the instructions in their respective README files
```

## Data Storage

### Response Files
All stock analysis responses are automatically saved to:
```
output/json/quant/quant_{session_id}_{timestamp}.json
```

### Conversation Memory
Conversations are stored in the shared SQLite checkpointer:
```
checkpoints.sqlite
```

## Integration with Document Analysis System

The Stock Market Analysis and Document Analysis systems are **independent but complementary**:

1. **Different Use Cases**
   - Document Analysis: Financial document Q&A (10-K reports, earnings transcripts, research papers)
   - Stock Analysis: Real-time market data, technical indicators, and analyst research

2. **Complementary Workflows**
   ```
   User: "What does Apple's latest 10-K say about revenue growth?"
   → Use Document Analysis: POST /ask
   
   User: "What is Apple's current stock price and P/E ratio?"
   → Use Stock Analysis: POST /quant/query
   
   User: "Compare Apple and Microsoft's financial performance"
   → Use Document Analysis: POST /compare
   
   User: "Show me technical analysis for AAPL vs MSFT"
   → Use Stock Analysis: POST /quant/query
   ```

3. **Unified Portfolio Context**
   - Both systems can work with the same portfolio
   - Document Analysis filters by portfolio companies
   - Stock Analysis provides market data for portfolio companies
   - Comprehensive investment insights from multiple angles

## Error Handling

### Graceful Degradation
- If MCP servers are unavailable, the system logs warnings but continues
- Requests will return appropriate errors if required servers are down
- Health endpoint shows which servers are available

### Common Errors

1. **503 Service Unavailable**
   - Stock agents not initialized
   - Check `/quant/health` for details

2. **404 Portfolio Not Found**
   - Invalid portfolio_id provided
   - Verify portfolio exists at `/portfolios/{id}`

3. **500 Internal Server Error**
   - Agent processing failure
   - Check logs for detailed traceback
   - May indicate MCP server issues

## Configuration

### Environment Variables

```bash
# Optional: Separate database for stock agents
STOCK_SQLITE_DB_PATH=sqlite:///checkpoints_stock.db

# Shared checkpointer (default)
# Uses main checkpoints.sqlite if not specified
```

### Port Configuration
The main API runs on port 8000 (default). The MCP servers use:
- 8565 (Stock Information)
- 8566 (Technical Analysis)
- 8567 (Research)

Ensure these ports are available or update the configuration in `app/services/stock_agent.py`.

## Testing

### 1. Test Health
```bash
curl http://localhost:8000/quant/health
```

### 2. Test Basic Query
```bash
curl -X POST http://localhost:8000/quant/query \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What is the current price of AAPL?",
    "user_id": "test_user"
  }'
```

### 3. Test Capabilities
```bash
curl http://localhost:8000/quant/capabilities
```

## Migration Notes

### Changes from Standalone API

1. **URL Prefix**: All endpoints now under `/quant`
   - Old: `http://localhost:8568/chat`
   - New: `http://localhost:8000/quant/query`

2. **Request Format**: Simplified and aligned with RAG system
   - Added `user_id` field (required)
   - Added `portfolio_id` field (optional)
   - Renamed `message` to `query`

3. **Shared Resources**: Uses main app's checkpointer
   - No need to manage separate database
   - Unified conversation memory

4. **Health Monitoring**: Two-level health checks
   - `/health` - Overall system health
   - `/quant/health` - Detailed stock system health

## Future Enhancements

1. **Database Integration**
   - Create `StockSession` model to track sessions in database
   - Link sessions to portfolios permanently
   - Query history and analytics

2. **Combined Queries**
   - Endpoint that queries both RAG and Quant systems
   - Unified response combining document analysis and market data

3. **Portfolio Recommendations**
   - Analyze portfolio companies using stock agents
   - Generate portfolio-level insights
   - Risk analysis and rebalancing suggestions

4. **Caching**
   - Cache frequently requested stock data
   - Reduce MCP server load
   - Faster response times

## Support

For issues or questions:
1. Check `/quant/health` for system status
2. Review logs for detailed error messages
3. Verify MCP servers are running and accessible
4. Check that all dependencies are installed

## Summary

The Stock Market Analysis system is now fully integrated into the Investment Analyst API:

✅ Unified platform for comprehensive investment analysis
✅ Document intelligence + Real-time market data
✅ Shared infrastructure and conversation memory
✅ Portfolio-centric architecture
✅ Graceful degradation and error handling
✅ Production-ready REST API
✅ Multi-agent orchestration

**Access the complete platform:** http://localhost:8000/docs

**Platform Capabilities:**
- 📄 Analyze financial documents with AI
- 📈 Track real-time stock prices and technical indicators
- 💼 Manage investment portfolios
- 🔗 Connect to multiple data sources
- 🤖 Multi-agent system for intelligent routing
- 💾 Persistent conversation memory
