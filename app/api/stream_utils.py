"""
SSE streaming utilities for agentic RAG and Quant UIs.

Provides node-to-milestone mappings and async generators that stream
LangGraph execution progress as Server-Sent Events.
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
# Nodes mapped to None are silent (no event emitted to the frontend).
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

TOTAL_STEPS = 5  # Logical max used for progress-bar UIs


# ---------------------------------------------------------------------------
# SSE formatting
# ---------------------------------------------------------------------------

def format_sse(event: str, data: dict) -> str:
    """Format a single Server-Sent Event."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


# ---------------------------------------------------------------------------
# Metadata extraction helpers
# ---------------------------------------------------------------------------

def _extract_answer(final_state: dict) -> str:
    """Pull the final answer string from accumulated graph state."""
    # show_result puts the answer in messages via AIMessage
    messages = final_state.get("messages")
    if messages:
        last = messages[-1] if isinstance(messages, list) else messages
        if hasattr(last, "content"):
            return last.content
        return str(last)
    # Fallback: Intermediate_message is set by generate node
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

    # Document count & source files
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
# Core SSE stream generator
# ---------------------------------------------------------------------------

async def rag_stream_generator(
    agent,
    inputs: dict,
    config: dict,
    thread_id: str,
    extra_metadata: dict,
):
    """
    Async generator that executes the LangGraph and yields SSE events.

    Events emitted
    --------------
    status   – after each visible node completes (milestone progress)
    answer   – the final answer text (+ chart_url if present)
    metadata – full result metadata (sources, flags, etc.)
    done     – signals stream end
    error    – on any exception

    DB persistence
    --------------
    The assistant message is saved in a *new* DB session created inside
    this generator (the FastAPI dependency-injected session may already be
    closed by the time the generator runs).
    """
    final_state: Dict[str, Any] = {}
    answer = ""

    try:
        # ----- stream graph execution -----
        async for chunk in agent.astream(inputs, config, stream_mode="updates"):
            for node_name, state_update in chunk.items():
                # Merge updates into accumulated state
                if isinstance(state_update, dict):
                    final_state.update(state_update)

                # Emit milestone event if applicable
                milestone = NODE_MILESTONES.get(node_name)
                if milestone is not None:
                    yield format_sse("status", {
                        "node": node_name,
                        "message": milestone["message"],
                        "step": milestone["step"],
                        "total_steps": TOTAL_STEPS,
                    })

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
        metadata = _build_metadata(final_state, extra_metadata)
        yield format_sse("metadata", metadata)

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
# Quant: Agent → user-facing milestone mapping
# ---------------------------------------------------------------------------
# The supervisor fires multiple times (once per routing decision).
# Sub-agent nodes are named after the agent itself (langgraph_supervisor pattern).

QUANT_AGENT_MILESTONES: Dict[str, Dict[str, Any]] = {
    "supervisor":               {"message": "Routing query to specialized agent...", "step": 1},
    "ticker_finder_agent":      {"message": "Resolving ticker symbol...",            "step": 2},
    "stock_information_agent":  {"message": "Fetching stock fundamentals...",        "step": 3},
    "technical_analysis_agent": {"message": "Running technical analysis...",         "step": 4},
    "research_agent":           {"message": "Searching analyst reports...",          "step": 4},
}

QUANT_TOTAL_STEPS = 4


def _get_message_text(msg) -> str:
    """Safely extract text from a LangChain message (handles multi-modal content lists)."""
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


def _extract_quant_answer(new_messages: list):
    """
    Find the last meaningful AI response from a sub-agent (not the supervisor,
    not handoff/transfer messages). Returns (answer_text, agent_name).
    """
    for msg in reversed(new_messages):
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

    # Fallback: last message that has any content
    for msg in reversed(new_messages):
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

    The quant system uses langgraph_supervisor which exposes each sub-agent
    as a named graph node. astream(stream_mode="updates") yields per-node
    state deltas, so we can map agent names → milestone messages.

    Events emitted
    --------------
    status   – when each agent node completes (routing + sub-agents)
    answer   – final answer text + which agent produced it
    metadata – session/portfolio context
    done     – stream end
    error    – on exception
    """
    new_messages: List[Any] = []
    answer = ""
    agent_used = None
    supervisor_count = 0

    try:
        async for chunk in supervisor.astream(inputs, config, stream_mode="updates"):
            for node_name, state_update in chunk.items():
                # Accumulate messages emitted by this node
                if isinstance(state_update, dict):
                    msgs = state_update.get("messages", [])
                    if isinstance(msgs, list):
                        new_messages.extend(msgs)

                # Emit milestone if this node has one
                milestone = QUANT_AGENT_MILESTONES.get(node_name)
                if milestone is not None:
                    label = milestone["message"]

                    if node_name == "supervisor":
                        supervisor_count += 1
                        if supervisor_count > 1:
                            # Distinguish routing calls from the final synthesis call:
                            # - Routing: supervisor emits an AIMessage with tool_calls
                            #   (transfer_to_<agent>) → still delegating to a sub-agent.
                            # - Synthesis: no tool_calls → supervisor is writing the
                            #   final aggregated response.
                            sup_msgs = (
                                state_update.get("messages", [])
                                if isinstance(state_update, dict) else []
                            )
                            is_routing = any(
                                getattr(m, "tool_calls", None)
                                for m in sup_msgs
                                if getattr(m, "type", None) == "ai"
                            )
                            label = (
                                "Routing to next agent..."
                                if is_routing
                                else "Synthesizing results..."
                            )

                    yield format_sse("status", {
                        "node": node_name,
                        "message": label,
                        "step": milestone["step"],
                        "total_steps": QUANT_TOTAL_STEPS,
                    })

        # Extract the best answer from accumulated messages
        answer, agent_used = _extract_quant_answer(new_messages)

        yield format_sse("answer", {
            "content": answer,
            "agent_used": agent_used,
        })

        yield format_sse("metadata", {
            **extra_metadata,
            "agent_used": agent_used,
            "message_count": len(new_messages),
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
                            "message_count": len(new_messages),
                        },
                    )
                    db.commit()
                finally:
                    db.close()
            except Exception:
                traceback.print_exc()
