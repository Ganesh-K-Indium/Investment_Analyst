import asyncio

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from app.services.graph_stream import stream_graph
from rag.graph.state import GraphState


def test_stream_graph_reports_steps_and_final_state():
    g = StateGraph(GraphState)
    g.add_node("generate", lambda s: {"Intermediate_message": "ok"})
    g.add_node("show_result", lambda s: {"messages": AIMessage(content="final")})
    g.add_edge(START, "generate"); g.add_edge("generate", "show_result"); g.add_edge("show_result", END)
    app = g.compile(checkpointer=MemorySaver())
    events = []

    async def emit(e):
        events.append(e)

    out = asyncio.run(stream_graph(app, {"messages": [HumanMessage(content="hi")]}, {"configurable": {"thread_id": "s"}}, emit))
    assert [e["node"] for e in events if e["type"] == "step"] == ["generate", "show_result"]
    assert out["messages"][-1].content == "final" and out["Intermediate_message"] == "ok"
