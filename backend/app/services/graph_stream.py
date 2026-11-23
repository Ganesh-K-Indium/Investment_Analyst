"""Run a LangGraph graph with astream and report progress, for the server-sent-event endpoints."""
from typing import Any, Awaitable, Callable, Dict, Iterable, Optional


async def stream_graph(agent, inputs, config, emit: Callable[[Dict[str, Any]], Awaitable[None]], answer_nodes: Optional[Iterable[str]] = None) -> Dict[str, Any]:
    """Report each finished step ({"type":"step","node"}) and, for the nodes in `answer_nodes`, the answer tokens as they are written
    ({"type":"token"}). Returns the final state, i.e. what ainvoke would have returned, including a pending clarification
    question under "__interrupt__"."""
    answer_nodes = set(answer_nodes or ())
    final: Optional[Dict[str, Any]] = None
    async for mode, payload in agent.astream(inputs, config, stream_mode=["values", "updates", "messages"]):
        if mode == "updates":
            for node in payload:
                if node != "__interrupt__":
                    await emit({"type": "step", "node": node})
        elif mode == "messages":
            chunk, meta = payload
            text = getattr(chunk, "content", "")
            if text and isinstance(text, str) and (not answer_nodes or meta.get("langgraph_node") in answer_nodes):
                await emit({"type": "token", "text": text})
        elif mode == "values":
            final = payload
    final = final or {}
    state = await agent.aget_state(config)
    pending = [i for t in (state.tasks or ()) for i in t.interrupts]
    return {**final, "__interrupt__": pending} if pending else final
