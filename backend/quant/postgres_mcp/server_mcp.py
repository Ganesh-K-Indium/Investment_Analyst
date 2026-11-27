"""
PostgreSQL MCP Server
---------------------------------------------------------------------------
Provides tools for analyzing PostgreSQL databases safely.
"""

import os
import sys
import json
import logging
from typing import Dict, Any, Optional

# Add project root to sys.path to allow imports from app.*
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from fastmcp import FastMCP

import psycopg2
from psycopg2.extras import RealDictCursor
import app.core.config  # noqa: F401,E402  (loads .env once; real environment variables win)


from app.core.logging_setup import configure_logging  # noqa: E402
configure_logging()
logger = logging.getLogger("quant.postgres_mcp.server_mcp")

# Initialize FastMCP server
postgres_server = FastMCP(
    "postgres",
    instructions="""
    # PostgreSQL Analysis MCP Server
    
    This server provides read-only capabilities for analyzing user PostgreSQL databases.
    - list_tables: Get a list of public tables.
    - get_schema_and_sample: Get column definitions and a data sample.
    - query_database: Execute a read-only SELECT query.
    """
)

async def _get_creds_async(access_token: str) -> Optional[Dict]:
    """Credentials for the integration named by a SIGNED token, and only if it belongs to the user the token was issued to."""
    from app.database.connection import SessionLocal
    from app.services.integration import IntegrationService
    from app.services.mcp_auth import AuthError, verify_integration

    try:
        user_id, integration_id = verify_integration(access_token)
    except AuthError:
        return None
    async with SessionLocal() as db:
        integration = await IntegrationService.get_integration(db, integration_id)
        if integration and integration.vendor == "postgresql" and str(integration.user_id) == user_id:
            return integration.credentials
        return None


@postgres_server.tool(
    name="list_tables",
    description="""
    List all user tables in the connected PostgreSQL database.
    
    Args:
        integration_id: str - The access token from the user's prompt ([SYSTEM: ... integration_id=...]). Pass it exactly as given.
    """
)
async def list_tables(integration_id: str) -> str:
    creds = await _get_creds_async(integration_id)
    if not creds:
        return "Error: Invalid or non-PostgreSQL integration."
        
    def _fetch_tables():
        conn = psycopg2.connect(
            host=creds.get("host"),
            port=creds.get("port"),
            dbname=creds.get("database"),
            user=creds.get("user"),
            password=creds.get("password"),
            options="-c default_transaction_read_only=on -c statement_timeout=15000", connect_timeout=10
        )
        cursor = conn.cursor()
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public';")
        tables = [row[0] for row in cursor.fetchall()]
        conn.close()
        return f"Tables: {', '.join(tables)}"

    try:
        import asyncio
        return await asyncio.to_thread(_fetch_tables)
    except Exception as e:
        return f"Database Error: {str(e)}"

@postgres_server.tool(
    name="get_schema_and_sample",
    description="""
    Retrieve column definitions, data types, and a 5-row sample for a given table.
    
    Args:
        table_name: str - The name of the table.
        integration_id: str - The access token from the user's prompt ([SYSTEM: ... integration_id=...]). Pass it exactly as given.
    """
)
async def get_schema_and_sample(table_name: str, integration_id: str) -> str:
    import re
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_$]{0,62}", table_name or ""):
        return "Error: invalid table name."  # the name is placed in SQL below, so only plain identifiers are accepted
    creds = await _get_creds_async(integration_id)
    if not creds:
        return "Error: Invalid or non-PostgreSQL integration."
        
    def _fetch_schema():
        conn = psycopg2.connect(
            host=creds.get("host"),
            port=creds.get("port"),
            dbname=creds.get("database"),
            user=creds.get("user"),
            password=creds.get("password"),
            options="-c default_transaction_read_only=on -c statement_timeout=15000", connect_timeout=10
        )
        cursor = conn.cursor()
        
        # Get columns
        cursor.execute(f"SELECT column_name, data_type FROM information_schema.columns WHERE table_name = '{table_name}';")
        columns = cursor.fetchall()
        schema_str = f"Table: {table_name}\nColumns:\n" + "\n".join([f"- {col[0]}: {col[1]}" for col in columns])
        
        # Get foreign keys
        fk_query = f"""
        SELECT
            kcu.column_name, 
            ccu.table_name AS foreign_table_name,
            ccu.column_name AS foreign_column_name 
        FROM 
            information_schema.table_constraints AS tc 
            JOIN information_schema.key_column_usage AS kcu
              ON tc.constraint_name = kcu.constraint_name
              AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
              ON ccu.constraint_name = tc.constraint_name
              AND ccu.table_schema = tc.table_schema
        WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_name='{table_name}';
        """
        try:
            cursor.execute(fk_query)
            fks = cursor.fetchall()
            if fks:
                schema_str += "\n\nForeign Keys:\n"
                for fk in fks:
                    schema_str += f"- {fk[0]} -> {fk[1]}.{fk[2]}\n"
        except Exception:
            pass
        
        # Get 5 row sample
        try:
            cursor = conn.cursor(cursor_factory=RealDictCursor)
            cursor.execute(f"SELECT * FROM {table_name} LIMIT 5;")
            rows = cursor.fetchall()
            schema_str += f"\n\nSample Data (5 rows):\n"
            for r in rows:
                schema_str += f"{dict(r)}\n"
        except Exception:
            schema_str += "\n\nCould not fetch sample data."

        conn.close()
        return schema_str

    try:
        import asyncio
        return await asyncio.to_thread(_fetch_schema)
    except Exception as e:
        return f"Database Error: {str(e)}"

@postgres_server.tool(
    name="query_database",
    description="""
    Execute a read-only SELECT query on the PostgreSQL database.
    Use this to answer user questions about their data.
    IMPORTANT RULES:
    1. Only SELECT queries are permitted.
    2. Write aggregation queries (GROUP BY, SUM, AVG) to analyze data instead of downloading millions of rows.
    3. The tool automatically enforces a LIMIT 100 on raw queries.
    
    Args:
        query: str - The SQL query to execute.
        integration_id: str - The access token from the user's prompt ([SYSTEM: ... integration_id=...]). Pass it exactly as given.
    """
)
async def query_database(query: str, integration_id: str) -> str:
    from app.services.mcp_auth import AuthError, check_select_only
    try:
        query = check_select_only(query)
    except AuthError as e:
        return f"Security Error: {e}"

    creds = await _get_creds_async(integration_id)
    if not creds:
        return "Error: Invalid or non-PostgreSQL integration."
        
    def _execute_query():
        conn = psycopg2.connect(
            host=creds.get("host"),
            port=creds.get("port"),
            dbname=creds.get("database"),
            user=creds.get("user"),
            password=creds.get("password"),
            options="-c default_transaction_read_only=on -c statement_timeout=15000", connect_timeout=10
        )
        cursor = conn.cursor(cursor_factory=RealDictCursor)
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()
        
        if not rows:
            return "Query executed successfully but returned 0 rows."
            
        res = f"Results ({len(rows)} rows):\n"
        for i, row in enumerate(rows):
            res += f"{dict(row)}\n"
            if i > 150:
                res += "... (truncated for context limits)\n"
                break
        return res

    try:
        import asyncio
        return await asyncio.to_thread(_execute_query)
    except Exception as e:
        return f"Execution Error: {str(e)}\nPlease rewrite the query to fix this error and try again."

if __name__ == "__main__":
    logger.info("Starting Postgres MCP Server...")
    from app.services import mcp_registry
    _srv = mcp_registry.server("postgres")
    postgres_server.run(transport="streamable-http", host=_srv["bind"], port=_srv["port"])
