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
                         detect_alpha_query, alpha_dimension_retrieve, alpha_generate_report,
                         detect_scenario_query, scenario_data_retrieve, scenario_generate_report,
                         detect_macro_query, macro_analyze_query,
                         macro_fetch_and_calculate, macro_format_answer)
from rag.graph.edges import (route_question, decide_to_generate,
                         decide_chart_generation,
                         route_alpha_workflow,
                         route_after_retrieve,
                         route_after_alpha_retrieve)
from rag.graph.benchmark import time_node, node_timer

logger = logging.getLogger("rag.graph.builder")

load_dotenv()
os.environ["GROQ_API_KEY"]=os.getenv("GROQ_API_KEY")
os.environ["TAVILY_API_KEY"]=os.getenv("TAVILY_API_KEY")

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

        # Add preprocessing node FIRST - analyzes query for sub-queries
        workflow.add_node("preprocess", time_node("preprocess")(preprocess_and_analyze_query))
        
        # Add ALPHA Framework nodes
        workflow.add_node("detect_alpha", time_node("detect_alpha")(detect_alpha_query))
        workflow.add_node("alpha_retrieve", time_node("alpha_retrieve")(alpha_dimension_retrieve))
        workflow.add_node("alpha_generate", time_node("alpha_generate")(alpha_generate_report))

        # Add Scenario Framework nodes (Bull / Bear / Base)
        workflow.add_node("detect_scenario", time_node("detect_scenario")(detect_scenario_query))
        workflow.add_node("scenario_retrieve", time_node("scenario_retrieve")(scenario_data_retrieve))
        workflow.add_node("scenario_generate", time_node("scenario_generate")(scenario_generate_report))
        
        # Add Macro Framework nodes (3-step pipeline)
        workflow.add_node("detect_macro", time_node("detect_macro")(detect_macro_query))
        workflow.add_node("macro_analyze", time_node("macro_analyze")(macro_analyze_query))
        workflow.add_node("macro_calculate", time_node("macro_calculate")(macro_fetch_and_calculate))
        workflow.add_node("macro_format", time_node("macro_format")(macro_format_answer))
        
        # Add nodes with timing decorators
        workflow.add_node("web_search", time_node("web_search")(web_search))
        workflow.add_node("retrieve", time_node("retrieve")(retrieve))
        workflow.add_node("grade_documents", time_node("grade_documents")(grade_documents))
        workflow.add_node("generate", time_node("generate")(generate))
        workflow.add_node("show_result", time_node("show_result")(show_result))
        workflow.add_node("integrate_web_search", time_node("integrate_web_search")(integrate_web_search))
        workflow.add_node("generate_chart", time_node("generate_chart")(generate_comparison_chart))
        
        # START -> detect_alpha (first: check for ALPHA buy-timing queries)
        workflow.add_edge(START, "detect_alpha")

        # detect_alpha -> detect_scenario (second: check for Bull/Bear/Base scenario queries)
        workflow.add_edge("detect_alpha", "detect_scenario")

        # detect_scenario -> detect_macro (third: check for Macro queries)
        workflow.add_edge("detect_scenario", "detect_macro")

        # detect_macro -> route_alpha_workflow: alpha | scenario | macro | normal
        workflow.add_conditional_edges(
            "detect_macro",
            route_alpha_workflow,
            {
                "alpha": "alpha_retrieve",
                "scenario": "scenario_retrieve",
                "macro": "macro_analyze",
                "normal": "preprocess",
            },
        )

        # ALPHA workflow: alpha_retrieve -> conditional -> alpha_generate / generate
        workflow.add_conditional_edges(
            "alpha_retrieve",
            route_after_alpha_retrieve,
            {
                "alpha_generate": "alpha_generate",
                "generate": "generate",
                "show_result": "show_result",   # insider_trading bypasses generate
            }
        )
        workflow.add_edge("alpha_generate", "show_result")

        # Scenario workflow: scenario_retrieve -> scenario_generate -> show_result -> END
        workflow.add_edge("scenario_retrieve", "scenario_generate")
        workflow.add_edge("scenario_generate", "show_result")
        
        # Macro workflow: macro_analyze -> macro_calculate -> macro_format -> show_result -> END
        workflow.add_edge("macro_analyze", "macro_calculate")
        workflow.add_edge("macro_calculate", "macro_format")
        workflow.add_edge("macro_format", "show_result")
        
        # Preprocess -> Router (Vectorstore vs WebSearch)
        workflow.add_conditional_edges(
            "preprocess",
            route_question,
            {
                "vectorstore": "retrieve",
                "web_search": "web_search",
            },
        )

        # Retrieve: comparison mode skips grading, normal mode grades documents
        workflow.add_conditional_edges(
            "retrieve",
            route_after_retrieve,
            {
                "generate": "generate",
                "grade_documents": "grade_documents",
            },
        )

        workflow.add_edge("web_search", "generate")

        workflow.add_conditional_edges(
            "grade_documents",
            decide_to_generate,
            {
                "generate": "generate",
                "integrate_web_search": "integrate_web_search",
            },
        )

        # integrate_web_search → generate directly (no re-grading)
        workflow.add_edge("integrate_web_search", "generate")

        # Generate always goes directly to chart decision (no hallucination grading)
        workflow.add_edge("generate", "decide_chart")

        # Add a decision node that routes to either chart generation or show_result
        workflow.add_node("decide_chart", lambda state: state)  # Pass-through node
        workflow.add_conditional_edges(
            "decide_chart",
            decide_chart_generation,
            {
                "generate_chart": "generate_chart",
                "show_result": "show_result"
            }
        )
        
        # Chart generation goes to show_result after completing
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
    