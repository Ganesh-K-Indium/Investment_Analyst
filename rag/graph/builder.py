"This module is useful for building the graph which will create an agentic workflow."
import os
import time
import asyncio
import logging
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import HumanMessage
from rag.graph.state import GraphState
from rag.graph.nodes import (web_search, retrieve,
                         grade_documents, generate,
                         show_result, integrate_web_search,
                         preprocess_and_analyze_query,
                         generate_comparison_chart,
                         alpha_dimension_retrieve, alpha_generate_report,
                         scenario_data_retrieve, scenario_generate_report, scenario_load_notes,
                         macro_analyze_query, macro_fetch_and_calculate, macro_format_answer)
from rag.graph.extras import (route_query, route_workflow, clarify, rewrite_query, verify_answer,
                         custom_retrieve, custom_generate, decide_after_grade)
from rag.graph.edges import (route_question, decide_chart_generation,
                         route_after_retrieve,
                         route_after_alpha_retrieve)
from rag.graph.benchmark import time_node, node_timer

logger = logging.getLogger("rag.graph.builder")

load_dotenv()
# Provider keys are optional: set them only when present, so a missing key fails where it is used instead of crashing at import.
for _key in ("GROQ_API_KEY", "TAVILY_API_KEY"):
    if os.getenv(_key):
        os.environ[_key] = os.getenv(_key)

class BuildingGraph:
    """
    This class has one class method which is responsible for building the graph
    """
    def __init__(self):
        pass
        
    async def get_graph(self, checkpointer=None):
        """
        This async method is responsible for creating the graph.
        
        Args:
            checkpointer: Optional checkpointer for memory/persistence
            
        Returns:
            app :- compiled graph
        """
        logger.info("Building context-free RAG graph...")
        
        workflow = StateGraph(GraphState)

        # --- nodes -------------------------------------------------------------------------------------------------
        def add(name, fn):
            workflow.add_node(name, time_node(name)(fn))

        add("route_query", route_query)          # one decision: ALPHA | scenario | macro | the user's own framework | standard RAG
        add("clarify", clarify)                  # optional pause to ask which company (human in the loop)
        add("preprocess", preprocess_and_analyze_query)

        # built-in frameworks
        add("alpha_retrieve", alpha_dimension_retrieve)
        add("alpha_generate", alpha_generate_report)
        add("scenario_retrieve", scenario_data_retrieve)
        add("scenario_notes", scenario_load_notes)
        add("scenario_generate", scenario_generate_report)
        add("macro_analyze", macro_analyze_query)
        add("macro_calculate", macro_fetch_and_calculate)
        add("macro_format", macro_format_answer)

        # the user's own frameworks
        add("custom_retrieve", custom_retrieve)
        add("custom_generate", custom_generate)

        # standard pipeline
        add("web_search", web_search)
        add("retrieve", retrieve)
        add("grade_documents", grade_documents)
        add("rewrite_query", rewrite_query)      # corrective retrieval
        add("generate", generate)
        add("verify_answer", verify_answer)      # figures in the answer vs the evidence
        add("integrate_web_search", integrate_web_search)
        add("generate_chart", generate_comparison_chart)
        add("show_result", show_result)

        # --- edges -------------------------------------------------------------------------------------------------
        workflow.add_edge(START, "route_query")
        workflow.add_conditional_edges(
            "route_query", route_workflow,
            {"alpha": "alpha_retrieve", "scenario": "scenario_retrieve", "macro": "macro_analyze", "custom": "custom_retrieve", "normal": "clarify"},
        )
        workflow.add_edge("clarify", "preprocess")

        # ALPHA: retrieve -> alpha_generate | generate (single pillar) | show_result (insider trading is pre-built)
        workflow.add_conditional_edges(
            "alpha_retrieve", route_after_alpha_retrieve,
            {"alpha_generate": "alpha_generate", "generate": "generate", "show_result": "show_result"},
        )
        workflow.add_edge("alpha_generate", "show_result")

        workflow.add_edge("scenario_retrieve", "scenario_notes")
        workflow.add_edge("scenario_notes", "scenario_generate")
        workflow.add_edge("scenario_generate", "show_result")

        workflow.add_edge("macro_analyze", "macro_calculate")
        workflow.add_edge("macro_calculate", "macro_format")
        workflow.add_edge("macro_format", "show_result")

        workflow.add_edge("custom_retrieve", "custom_generate")
        workflow.add_edge("custom_generate", "verify_answer")

        # standard: preprocess -> vectorstore | web
        workflow.add_conditional_edges("preprocess", route_question, {"vectorstore": "retrieve", "web_search": "web_search"})
        workflow.add_conditional_edges("retrieve", route_after_retrieve, {"generate": "generate", "grade_documents": "grade_documents"})
        workflow.add_edge("web_search", "generate")

        # grade -> generate | rewrite and search again (user's retry count) | web fallback (if the user allows it)
        workflow.add_conditional_edges(
            "grade_documents", decide_after_grade,
            {"generate": "generate", "rewrite": "rewrite_query", "integrate_web_search": "integrate_web_search"},
        )
        workflow.add_edge("rewrite_query", "retrieve")
        workflow.add_edge("integrate_web_search", "generate")

        workflow.add_edge("generate", "verify_answer")
        workflow.add_conditional_edges(
            "verify_answer", decide_chart_generation,
            {"generate_chart": "generate_chart", "show_result": "show_result"},
        )
        workflow.add_edge("generate_chart", "show_result")
        workflow.add_edge("show_result", END)

        # Compile with checkpointer for memory and HITL interrupts
        # Compile with checkpointer for memory
        if checkpointer:
            app = workflow.compile(
                checkpointer=checkpointer
            )
            logger.info("Graph compiled successfully (WITH Checkpointer/Memory)")
        else:
            app = workflow.compile()
            logger.info("Graph compiled successfully (context-free mode)")
        
        return app
    
    
    async def cleanup(self):
        """No cleanup needed in context-free mode""" 
        pass

async def main():
    """Main async function to run the graph with memory"""
    graph_obj = BuildingGraph()
    
    try:
        # Initialize graph with memory
        agent = await graph_obj.get_graph()
        
        # Configure thread for conversation memory
        thread_id = "conversation_1"  # You can generate unique IDs for different conversations
        config = {"configurable": {"thread_id": thread_id}}
        
        inputs = {
            "messages": [HumanMessage(content="""tell me about the distribution of discovery projects across various 
                         phases of the R&D pipeline along with the timeline and number of projects for pfizer?""")],
            "vectorstore_searched": False,
            "web_searched": False,
            "vectorstore_quality": "none",
            "needs_web_fallback": False,
            "retry_count": 0,
            "document_sources": {},
            "citation_info": [],
            "summary_strategy": "single_source",
            "sub_query_analysis": {},
            "sub_query_results": {}
        }
        
        # Start timing the entire workflow
        node_timer.start_total_timer()
        
        # Invoke with config for memory
        messages = await agent.ainvoke(inputs, config)
        
        # Print timing summary
        node_timer.print_summary()
        
        logger.info("\n" + "="*50)
        logger.info("FINAL RESULT:")
        logger.info("="*50)
        logger.info(messages["messages"][-1].content)
        
    finally:
        # Always cleanup
        await graph_obj.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
    