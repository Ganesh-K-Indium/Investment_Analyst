"""
Shared agent/graph initialization, used by both the API process (app/main.py)
and the background job worker (app/worker.py) so they build the identical
RAG graph + quant supervisor against the identical checkpointer, instead of
duplicating (and risking drift in) that startup logic.
"""
import logging

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.database.connection import DATABASE_URL, _to_sync_url
from rag.graph.builder import BuildingGraph
from app.services.stock_agent import initialize_stock_agents

logger = logging.getLogger("app.core.agents_init")


class AgentBundle:
    """Everything a process needs to run RAG/quant agent invocations."""

    def __init__(self):
        self.checkpointer_context = None
        self.checkpointer = None
        self.graph_obj: BuildingGraph | None = None
        self.rag_agent = None
        self.stock_supervisor = None
        self.stock_agents_ready = False

    async def build(self):
        logger.info("Initializing shared Postgres LangGraph checkpointer...")
        pg_conn_string = _to_sync_url(DATABASE_URL)
        self.checkpointer_context = AsyncPostgresSaver.from_conn_string(pg_conn_string)
        self.checkpointer = await self.checkpointer_context.__aenter__()
        await self.checkpointer.setup()

        logger.info("Building RAG graph...")
        self.graph_obj = BuildingGraph()
        self.rag_agent = await self.graph_obj.get_graph(checkpointer=self.checkpointer)

        logger.info("Initializing Stock Analysis System...")
        try:
            self.stock_supervisor, self.stock_agents_ready = await initialize_stock_agents(
                checkpointer=self.checkpointer
            )
            if not self.stock_agents_ready:
                logger.warning("Stock Analysis System not available — start MCP servers and retry")
        except Exception as e:
            logger.warning("Failed to initialize Stock Analysis System: %s", e)
            self.stock_supervisor = None
            self.stock_agents_ready = False

        return self

    async def close(self):
        if self.checkpointer_context:
            await self.checkpointer_context.__aexit__(None, None, None)
        if self.graph_obj:
            await self.graph_obj.cleanup()
