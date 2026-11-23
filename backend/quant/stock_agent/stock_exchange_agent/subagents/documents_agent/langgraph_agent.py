"""
Documents Agent: the user's own uploaded files (statements, broker notes, models, scans) for the quant supervisor.

Unlike the market-data agents it has no MCP server: its tools run in this process and act for the signed-in user, whose id comes from
the run config (`configurable.user_id`), never from the model. It can only see documents that user owns or that were shared with the
workspace.
"""
import json
import logging

from langchain.agents import create_agent
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.services.quant_runtime import RoleChatModel

logger = logging.getLogger("quant.stock_agent.stock_exchange_agent.subagents.documents_agent")


def _user(config: RunnableConfig):
    return ((config or {}).get("configurable") or {}).get("user_id")


@tool
async def list_my_documents(config: RunnableConfig, entity: str = "", doc_type: str = "") -> str:
    """List the user's uploaded documents (id, name, type, company, period, pages, status). Optionally filter by company/ticker or document type."""
    uid = _user(config)
    if not uid:
        return "No signed-in user in this request, so no documents can be listed."
    from sqlalchemy import or_, select
    from app.database.connection import SessionLocal
    from app.database.models import UserDocument
    async with SessionLocal() as db:
        cond = [or_(UserDocument.user_id == str(uid), UserDocument.visibility == "workspace")]
        if entity:
            cond.append(UserDocument.entity == entity.upper())
        if doc_type:
            cond.append(UserDocument.doc_type == doc_type)
        rows = (await db.execute(select(UserDocument).where(*cond).order_by(UserDocument.created_at.desc()).limit(50))).scalars().all()
    if not rows:
        return "The user has no uploaded documents" + (f" for {entity}." if entity else ".")
    return json.dumps([{"id": d.id, "name": d.filename, "type": d.doc_type, "company": d.entity, "period": d.period, "pages": d.pages, "status": d.status} for d in rows])


@tool
async def search_my_documents(query: str, config: RunnableConfig, entity: str = "", limit: int = 6) -> str:
    """Search the user's uploaded documents for passages relevant to `query` (hybrid keyword + semantic). Returns the passages with file name, page and units.
    Always quote the file name and page when you use a passage, and keep the stated units (thousands/millions)."""
    uid = _user(config)
    if not uid:
        return "No signed-in user in this request, so no documents can be searched."
    from app.services.user_docs import store
    docs = await store.search(uid, query, limit=max(1, min(int(limit), 10)), entities=[entity.upper()] if entity else None)
    if not docs:
        return "No matching passages in the user's uploaded documents."
    return "\n\n---\n\n".join(f"{d.page_content}" for d in docs)


@tool
async def get_document_tables(document_id: str, config: RunnableConfig, page: int = 0) -> str:
    """Return the tables of one uploaded document as markdown (all pages, or one `page`), with units. Use this to read exact figures from a statement or model."""
    uid = _user(config)
    if not uid:
        return "No signed-in user in this request."
    from sqlalchemy import or_
    from app.database.connection import SessionLocal
    from app.database.models import UserDocument
    from app.services.user_docs import chunks as ch, manager
    async with SessionLocal() as db:
        d = await db.get(UserDocument, document_id)
    if d is None or (str(d.user_id) != str(uid) and d.visibility != "workspace"):
        return "Document not found."
    result = manager.load_result(d.id)
    if result is None:
        return "This document has not been read yet."
    base = manager.base_meta(d, result)
    out = []
    for p in result["pages"]:
        if page and p["page"] != page:
            continue
        for c in ch.page_chunks(result, p["page"], base, d.corrections or {}):
            if c["metadata"]["chunk_kind"] == "table":
                out.append(f"(page {p['page']})\n{c['text'].replace(ch.TABLE_PREFIX, '', 1)}")
    return "\n\n".join(out)[:60000] or "No tables found in this document."


async def create_documents_agent(checkpointer=None):
    system_prompt = """You are the Documents Agent. You work ONLY with files the user uploaded (statements, broker notes, models, scans, decks).

TOOLS: list_my_documents, search_my_documents, get_document_tables.

RULES:
- Start with search_my_documents for a question; use list_my_documents to see what exists; use get_document_tables when exact figures from a table are needed.
- Cite every figure as (file name, page). Keep the units the document states ("in thousands", "in millions") and the currency; never convert or mix scales silently.
- A passage marked low confidence has not been confirmed by a person: say so when you rely on it.
- These are the user's own files, not market data: do not mix them with live prices unless the supervisor asks you to, and never invent values a document does not contain.
- If nothing matches, say so plainly and suggest uploading the document or naming the company."""
    return create_agent(model=RoleChatModel(role="quant_agent"), tools=[list_my_documents, search_my_documents, get_document_tables],
                        name="documents_agent", system_prompt=system_prompt, checkpointer=checkpointer)
