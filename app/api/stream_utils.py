"""
SSE streaming utilities for agentic RAG and Quant UIs.

Provides node-to-milestone mappings and async generators that stream
LangGraph execution progress as Server-Sent Events.

SSE event types emitted
-----------------------
thinking   – real-time LLM token chunk from a node (Google ADK style)
tool_call  – an agent is calling a tool (name visible immediately)
status     – node-completion milestone (progress bar update)
answer     – final answer text + optional chart_url
metadata   – full result metadata (sources, flags, etc.)
done       – signals stream end
error      – on any exception
"""
import json
import traceback
from typing import Any, Dict, List, Optional

from app.database.connection import SessionLocal
from app.database.models import MessageRole
from app.services.chat import ChatService


# ---------------------------------------------------------------------------
# RAG: Node → user-facing milestone mapping
# ---------------------------------------------------------------------------
# Nodes mapped to None are silent (no status event emitted to the frontend).
# "step" is a logical progress indicator (1-5 scale).

NODE_MILESTONES: Dict[str, Optional[Dict[str, Any]]] = {
    # Normal RAG flow
    "detect_alpha":         {"message": "Detecting analysis type...",           "step": 1},
    "detect_scenario":      None,
    "preprocess":           {"message": "Analyzing your query...",              "step": 2},
    "retrieve":             {"message": "Retrieving relevant documents...",     "step": 3},
    "grade_documents":      {"message": "Evaluating document quality...",       "step": 4},
    "gap_analysis":         {"message": "Identifying missing information...",   "step": 4},
    "web_search":           {"message": "Searching the web...",                 "step": 3},
    "financial_web_search": {"message": "Searching financial sources...",       "step": 4},
    "integrate_web_search": {"message": "Integrating web results...",           "step": 4},
    "generate":             {"message": "Generating investment analysis...",    "step": 5},
    "decide_chart":         None,
    "generate_chart":       {"message": "Creating comparison chart...",         "step": 5},
    "show_result":          None,
    # ALPHA workflow
    "alpha_retrieve":       {"message": "Retrieving ALPHA framework data...",   "step": 3},
    "alpha_generate":       {"message": "Generating ALPHA report...",           "step": 5},
    # Scenario workflow
    "scenario_retrieve":    {"message": "Collecting scenario data...",          "step": 3},
    "scenario_generate":    {"message": "Building scenario analysis...",        "step": 5},
}

TOTAL_STEPS = 5

# Human-readable display names for RAG nodes (used in thinking/tool_call events)
RAG_NODE_DISPLAY: Dict[str, str] = {
    "detect_alpha":         "Analysis Type Detector",
    "detect_scenario":      "Scenario Detector",
    "preprocess":           "Query Preprocessor",
    "retrieve":             "Document Retriever",
    "grade_documents":      "Document Grader",
    "gap_analysis":         "Gap Analyzer",
    "web_search":           "Web Search",
    "financial_web_search": "Financial Web Search",
    "integrate_web_search": "Web Integration",
    "generate":             "Investment Analyst",
    "decide_chart":         "Chart Planner",
    "generate_chart":       "Chart Generator",
    "show_result":          "Result",
    "alpha_retrieve":       "ALPHA Data Retriever",
    "alpha_generate":       "ALPHA Analyst",
    "scenario_retrieve":    "Scenario Data Retriever",
    "scenario_generate":    "Scenario Analyst",
}


# ---------------------------------------------------------------------------
# Quant: Agent → user-facing milestone mapping
# ---------------------------------------------------------------------------

QUANT_AGENT_MILESTONES: Dict[str, Dict[str, Any]] = {
    "supervisor":               {"message": "Routing query to specialized agent...", "step": 1},
    "ticker_finder_agent":      {"message": "Resolving ticker symbol...",            "step": 2},
    "stock_information_agent":  {"message": "Fetching stock fundamentals...",        "step": 3},
    "technical_analysis_agent": {"message": "Running technical analysis...",         "step": 4},
    "research_agent":           {"message": "Searching analyst reports...",          "step": 4},
}

QUANT_TOTAL_STEPS = 4

# Human-readable display names for Quant agent nodes
QUANT_NODE_DISPLAY: Dict[str, str] = {
    "supervisor":               "Supervisor",
    "ticker_finder_agent":      "Ticker Finder",
    "stock_information_agent":  "Stock Information Agent",
    "technical_analysis_agent": "Technical Analysis Agent",
    "research_agent":           "Research Agent",
}


# ---------------------------------------------------------------------------
# SSE formatting
# ---------------------------------------------------------------------------

def format_sse(event: str, data: dict) -> str:
    """Format a single Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------------------
# Streaming helpers
# ---------------------------------------------------------------------------

def _get_chunk_text(msg_chunk) -> str:
    """Extract text content from a streaming LangChain message chunk."""
    content = getattr(msg_chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts)
    return ""


def _get_outer_node(metadata: dict) -> str:
    """
    Resolve the outermost (supervisor-level) node name from LangGraph message metadata.

    When sub-agents run as nested subgraphs, LangGraph sets:
      langgraph_node             = inner node name  (e.g. "agent", "tools")
      langgraph_checkpoint_ns    = "outer_node:checkpoint_id"

    We want the outer node name so thinking/tool_call events are attributed
    to the right visible agent rather than to internal node names.
    """
    ns = metadata.get("langgraph_checkpoint_ns", "")
    node = metadata.get("langgraph_node", "")

    if not ns:
        return node

    # ns format: "outer_node:checkpoint_id"  or  "outer_node:id|inner_node:id"
    first_part = ns.split("|")[0]
    outer_node = first_part.split(":")[0]
    return outer_node or node


# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------

def _extract_answer(final_state: dict) -> str:
    """Pull the final answer string from accumulated graph state."""
    messages = final_state.get("messages")
    if messages:
        last = messages[-1] if isinstance(messages, list) else messages
        if hasattr(last, "content"):
            return last.content
        return str(last)
    return final_state.get("Intermediate_message", "")


def _build_metadata(final_state: dict, extra: dict) -> dict:
    """Build the metadata payload sent after the answer."""
    meta = {**extra}
    for key in (
        "vectorstore_searched", "web_searched", "vectorstore_quality",
        "needs_web_fallback", "retry_count", "summary_strategy",
        "sub_query_analysis", "sub_query_results", "document_sources",
        "citation_info", "tool_calls",
    ):
        meta[key] = final_state.get(key)

    docs = final_state.get("documents", [])
    meta["document_count"] = len(docs) if docs else 0
    meta["sources"] = [
        doc.metadata.get("source_file", "Unknown")
        for doc in (docs or [])
        if hasattr(doc, "metadata")
    ][:10]

    return meta


def _build_assistant_metadata(final_state: dict, extra: dict) -> dict:
    """Build metadata dict for persisting the assistant ChatMessage."""
    docs = final_state.get("documents", [])
    return {
        **extra,
        "vectorstore_searched": final_state.get("vectorstore_searched", False),
        "web_searched": final_state.get("web_searched", False),
        "vectorstore_quality": final_state.get("vectorstore_quality", "none"),
        "needs_web_fallback": final_state.get("needs_web_fallback", False),
        "retry_count": final_state.get("retry_count", 0),
        "summary_strategy": final_state.get("summary_strategy", "single_source"),
        "document_count": len(docs) if docs else 0,
        "sources": [
            doc.metadata.get("source_file", "Unknown")
            for doc in (docs or [])
            if hasattr(doc, "metadata")
        ][:5],
        "citation_info": final_state.get("citation_info", []),
        "document_sources": final_state.get("document_sources", {}),
        "documents": [
            {"metadata": doc.metadata if hasattr(doc, "metadata") else {}}
            for doc in (docs or [])
        ],
        "sub_query_analysis": final_state.get("sub_query_analysis", {}),
        "sub_query_results": final_state.get("sub_query_results", {}),
        "tool_calls": final_state.get("tool_calls", []),
        "intermediate_message": final_state.get("Intermediate_message", ""),
        "ticker": final_state.get("ticker"),
    }


# ---------------------------------------------------------------------------
# Core RAG SSE stream generator
# ---------------------------------------------------------------------------

async def rag_stream_generator(
    agent,
    inputs: dict,
    config: dict,
    thread_id: str,
    extra_metadata: dict,
):
    """
    Async generator that executes the RAG LangGraph and yields SSE events.

    Uses stream_mode=["updates", "messages"] so that:
    - "messages" mode delivers per-token LLM chunks in real time (thinking/tool_call events)
    - "updates"  mode delivers node-completion signals           (status events)

    Events emitted
    --------------
    thinking  – real-time LLM token from a node (Google ADK style)
    tool_call – when a node calls a tool
    status    – after each visible node completes
    answer    – the final answer text + optional chart URL
    metadata  – full result metadata
    done      – stream end signal
    error     – on any exception

    DB persistence
    --------------
    The assistant message is saved in a new DB session created inside this
    generator (the FastAPI dependency-injected session may already be closed).
    """
    final_state: Dict[str, Any] = {}
    answer = ""
    node_turn_tools: Dict[str, set] = {}   # node → set of tool names seen this turn

    try:
        async for mode, data in agent.astream(inputs, config, stream_mode=["updates", "messages"]):

            # ── Real-time LLM token stream ──────────────────────────────────
            if mode == "messages":
                msg_chunk, msg_meta = data
                node_name = _get_outer_node(msg_meta)
                label = RAG_NODE_DISPLAY.get(node_name, node_name)

                # Emit text chunk if present
                content = _get_chunk_text(msg_chunk)
                if content:
                    yield format_sse("thinking", {
                        "node": node_name,
                        "label": label,
                        "content": content,
                    })

                # Emit tool_call when a new tool name first appears
                for tc in (getattr(msg_chunk, "tool_call_chunks", None) or []):
                    tool_name = tc.get("name", "")
                    if tool_name:
                        node_turn_tools.setdefault(node_name, set())
                        if tool_name not in node_turn_tools[node_name]:
                            node_turn_tools[node_name].add(tool_name)
                            yield format_sse("tool_call", {
                                "node": node_name,
                                "label": label,
                                "tool": tool_name,
                            })

            # ── Node-completion updates ─────────────────────────────────────
            elif mode == "updates":
                for node_name, state_update in data.items():
                    if isinstance(state_update, dict):
                        final_state.update(state_update)

                    milestone = NODE_MILESTONES.get(node_name)
                    if milestone is not None:
                        yield format_sse("status", {
                            "node": node_name,
                            "label": RAG_NODE_DISPLAY.get(node_name, node_name),
                            "message": milestone["message"],
                            "step": milestone["step"],
                            "total_steps": TOTAL_STEPS,
                        })

                    # Reset tool tracking for next turn of this node
                    node_turn_tools.pop(node_name, None)

        # ----- extract answer -----
        answer = _extract_answer(final_state)

        # ----- send answer event -----
        answer_payload: Dict[str, Any] = {"content": answer}
        chart_url = final_state.get("chart_url")
        if chart_url:
            answer_payload["chart_url"] = chart_url
            answer_payload["chart_filename"] = final_state.get("chart_filename")
        yield format_sse("answer", answer_payload)

        # ----- send metadata event -----
        yield format_sse("metadata", _build_metadata(final_state, extra_metadata))

        # ----- signal completion -----
        yield format_sse("done", {})

    except Exception as e:
        traceback.print_exc()
        yield format_sse("error", {"message": str(e)})

    finally:
        # ----- persist assistant message (own DB session) -----
        if answer:
            try:
                db = SessionLocal()
                try:
                    ChatService.add_message(
                        db=db,
                        session_id=thread_id,
                        role=MessageRole.ASSISTANT,
                        content=answer,
                        metadata=_build_assistant_metadata(final_state, extra_metadata),
                    )
                    db.commit()
                finally:
                    db.close()
            except Exception:
                traceback.print_exc()


# ---------------------------------------------------------------------------
# Quant: message text helper (for final answer extraction from full messages)
# ---------------------------------------------------------------------------

def _get_message_text(msg) -> str:
    """Safely extract text from a complete LangChain message (handles multi-modal content)."""
    content = getattr(msg, "content", "")
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(block.get("text", ""))
        return " ".join(parts)
    return str(content)


def _extract_quant_answer(messages: list):
    """
    Find the last meaningful AI response from a sub-agent (not the supervisor,
    not handoff/transfer messages). Returns (answer_text, agent_name).
    """
    for msg in reversed(messages):
        if getattr(msg, "type", None) != "ai":
            continue
        name = getattr(msg, "name", None)
        if name == "supervisor":
            continue
        text = _get_message_text(msg)
        if not text:
            continue
        if text.startswith("Transferring back") or text.startswith("Successfully transferred"):
            continue
        return text, name

    # Fallback: last message with any content
    for msg in reversed(messages):
        text = _get_message_text(msg)
        if text:
            return text, getattr(msg, "name", None)

    return "", None


# ---------------------------------------------------------------------------
# Quant SSE stream generator
# ---------------------------------------------------------------------------

async def quant_stream_generator(
    supervisor,
    inputs: dict,
    config: dict,
    session_id: str,
    extra_metadata: dict,
):
    """
    Async generator for the multi-agent quant supervisor.

    Uses stream_mode=["updates", "messages"] so that:
    - "messages" mode delivers per-token LLM chunks as each agent thinks/responds
    - "updates"  mode delivers node-completion signals for milestone status events

    Supervisor routing vs synthesizing is detected automatically from the tool
    calls observed during each supervisor turn:
      - transfer_to_<non-supervisor> tool call → "Routing to specialized agent..."
      - no such transfer call              → "Synthesizing results..."

    Every supervisor invocation emits a status event (nothing is silenced).

    Events emitted
    --------------
    thinking  – real-time LLM token from a specific agent (Google ADK style)
    tool_call – when an agent calls a tool (MCP tools, handoff transfers)
    status    – agent-node completion milestone (all nodes, all turns)
    answer    – final answer text + which sub-agent produced it
    metadata  – session/portfolio context
    done      – stream end
    error     – on exception
    """
    all_messages: List[Any] = []
    answer = ""
    agent_used = None
    node_turn_tools: Dict[str, set] = {}   # node → tool names seen in current turn

    try:
        async for mode, data in supervisor.astream(inputs, config, stream_mode=["updates", "messages"]):

            # ── Real-time LLM token stream ──────────────────────────────────
            if mode == "messages":
                msg_chunk, msg_meta = data
                node_name = _get_outer_node(msg_meta)
                label = QUANT_NODE_DISPLAY.get(node_name, node_name)

                # Emit text chunk if present
                content = _get_chunk_text(msg_chunk)
                if content:
                    yield format_sse("thinking", {
                        "node": node_name,
                        "label": label,
                        "content": content,
                    })

                # Emit tool_call when a new tool name first appears this turn
                for tc in (getattr(msg_chunk, "tool_call_chunks", None) or []):
                    tool_name = tc.get("name", "")
                    if tool_name:
                        node_turn_tools.setdefault(node_name, set())
                        if tool_name not in node_turn_tools[node_name]:
                            node_turn_tools[node_name].add(tool_name)
                            yield format_sse("tool_call", {
                                "node": node_name,
                                "label": label,
                                "tool": tool_name,
                            })

            # ── Node-completion updates ─────────────────────────────────────
            elif mode == "updates":
                for node_name, state_update in data.items():
                    # Accumulate full messages for final answer extraction
                    if isinstance(state_update, dict):
                        msgs = state_update.get("messages", [])
                        if isinstance(msgs, list):
                            all_messages.extend(msgs)

                    # Determine milestone label
                    turn_tools = node_turn_tools.get(node_name, set())

                    if node_name == "supervisor":
                        # Routing turn: supervisor called transfer_to_<non-supervisor>
                        routing_transfers = {
                            t for t in turn_tools
                            if t.startswith("transfer_to_") and "supervisor" not in t
                        }
                        message = (
                            "Routing to specialized agent..."
                            if routing_transfers
                            else "Synthesizing results..."
                        )
                        step = QUANT_AGENT_MILESTONES["supervisor"]["step"]
                    else:
                        milestone = QUANT_AGENT_MILESTONES.get(node_name)
                        if milestone is None:
                            node_turn_tools.pop(node_name, None)
                            continue
                        message = milestone["message"]
                        step = milestone["step"]

                    yield format_sse("status", {
                        "node": node_name,
                        "label": QUANT_NODE_DISPLAY.get(node_name, node_name),
                        "message": message,
                        "step": step,
                        "total_steps": QUANT_TOTAL_STEPS,
                    })

                    # Reset tool tracking for next turn of this node
                    node_turn_tools.pop(node_name, None)

        # ----- extract best answer from accumulated messages -----
        answer, agent_used = _extract_quant_answer(all_messages)

        yield format_sse("answer", {
            "content": answer,
            "agent_used": agent_used,
        })

        yield format_sse("metadata", {
            **extra_metadata,
            "agent_used": agent_used,
            "message_count": len(all_messages),
        })

        yield format_sse("done", {})

    except Exception as e:
        traceback.print_exc()
        yield format_sse("error", {"message": str(e)})

    finally:
        if answer:
            try:
                db = SessionLocal()
                try:
                    ChatService.add_message(
                        db=db,
                        session_id=session_id,
                        role=MessageRole.ASSISTANT,
                        content=answer,
                        metadata={
                            **extra_metadata,
                            "agent_used": agent_used,
                            "message_count": len(all_messages),
                        },
                    )
                    db.commit()
                finally:
                    db.close()
            except Exception:
                traceback.print_exc()
