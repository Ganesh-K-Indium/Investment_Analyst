"""
PostgreSQL Agent - LangGraph Implementation
Handles database schema extraction and safe SQL querying
using MCP tools via LangGraph React Agent
"""
import asyncio
import logging
from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.tools import load_mcp_tools
from langchain.agents import create_agent
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("quant.stock_agent.stock_exchange_agent.subagents.postgres_agent")

async def create_postgres_agent(checkpointer=None):
    """Create the PostgreSQL sub-agent with all MCP tools."""
    system_prompt = """You are an expert Data Analyst Agent specializing in PostgreSQL databases.
Your job is to explore the user's private PostgreSQL database, understand its schema, and execute precise SQL queries to answer their analytical questions.

**AVAILABLE TOOLS (via MCP):**
- `list_tables`: List tables in the database.
- `get_schema_and_sample`: Get column definitions, types, and a 5-row sample.
- `query_database`: Execute a read-only SELECT query.

**IMPORTANT:**
All postgres tools require an `integration_id` argument. You will be provided this ID in the user's prompt (e.g., [SYSTEM: ... use integration_id=123]). You MUST always pass this exact ID to the tools.

**ANALYSIS WORKFLOW (MANDATORY):**
1. Always start by using `list_tables` if you don't know the schema yet.
2. If the user asks a question, use `get_schema_and_sample` on the relevant tables to ensure you are using the correct column names and understand how the data is stored (e.g. date formats).
3. Construct your SQL query and execute it using `query_database`.
4. If `query_database` returns an "Execution Error", read the error carefully, fix your SQL syntax or column names, and call `query_database` again. You are expected to self-heal.
5. If you cannot find data or there is a mismatch (e.g., the user's requested companies do not match the tickers in the database), explicitly and politely ask the user for clarification or guidance on how they want to proceed.
6. Provide a clear, synthesized answer to the user based on the results.

**BIG DATA RULES:**
- DO NOT run `SELECT * FROM massive_table` to do analysis in your head.
- ALWAYS push computations to the database using aggregations (`COUNT()`, `SUM()`, `AVG()`, `GROUP BY`, `ORDER BY LIMIT 10`).
- If you need to see raw data, the `query_database` tool will automatically limit it to 100 rows.

**SECURITY RULES:**
- You are strictly prohibited from generating `INSERT`, `UPDATE`, `DELETE`, `DROP`, or `ALTER` queries.
- Only generate `SELECT` queries (or `WITH` CTEs).

**FORMATTING RULES:**
- ALWAYS present database rows and schema definitions to the user as clean, properly formatted Markdown tables.
- NEVER output raw JSON, lists of dictionaries, or unformatted text blocks when displaying data.
"""
    
    model = ChatOpenAI(model="gpt-4o", temperature=0)
    MCP_HTTP_STREAM_URL = "http://localhost:8570/mcp"  # Postgres MCP server
    
    # Keep the client and session open for the lifetime of the agent
    client = streamablehttp_client(MCP_HTTP_STREAM_URL)
    read_stream, write_stream, _ = await client.__aenter__()
    session = ClientSession(read_stream, write_stream)
    await session.__aenter__()
    await session.initialize()
    tools = await load_mcp_tools(session)
    
    agent = create_agent(
        model=model,
        tools=tools,
        name="postgres_agent",
        system_prompt=system_prompt,
        checkpointer=checkpointer
    )
    
    # Attach the session and client to the agent to keep them alive
    agent._mcp_session = session
    agent._mcp_client = client
    
    return agent
