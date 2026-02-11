"This module is useful for building the graph which will create an agentic workflow."
import os
import time
import asyncio
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langchain_core.messages import HumanMessage
from rag.graph.state import GraphState
from rag.graph.nodes import (web_search, retrieve,
                         grade_documents, generate, transform_query,
                         financial_web_search, show_result, integrate_web_search,
                         evaluate_vectorstore_quality,
                         preprocess_and_analyze_query,
                         generate_comparison_chart,
                         detect_alpha_query, alpha_dimension_retrieve, alpha_generate_report)
from rag.graph.edges import (route_question, decide_to_generate,
                         grade_generation_v_documents_and_question,
                         decide_after_web_integration, decide_chart_generation,
                         route_alpha_workflow)
from rag.graph.benchmark import time_node, node_timer
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
        print("Building context-free RAG graph...")
        
        workflow = StateGraph(GraphState)

        # ... (rest of the node/edge definitions remain valid) ...
        # [Truncated only for this replace call, but effectively we want to update the end of function]

        # Add preprocessing node FIRST - analyzes query for sub-queries
        workflow.add_node("preprocess", time_node("preprocess")(preprocess_and_analyze_query))
        
        # Add ALPHA Framework nodes
        workflow.add_node("detect_alpha", time_node("detect_alpha")(detect_alpha_query))
        workflow.add_node("alpha_retrieve", time_node("alpha_retrieve")(alpha_dimension_retrieve))
        workflow.add_node("alpha_generate", time_node("alpha_generate")(alpha_generate_report))
        
        # Add nodes with timing decorators
        workflow.add_node("web_search", time_node("web_search")(web_search))
        workflow.add_node("retrieve", time_node("retrieve")(retrieve))
        workflow.add_node("grade_documents", time_node("grade_documents")(grade_documents))
        workflow.add_node("generate", time_node("generate")(generate))
        workflow.add_node("transform_query", time_node("transform_query")(transform_query))
        workflow.add_node("financial_web_search", time_node("financial_web_search")(financial_web_search))
        workflow.add_node("show_result", time_node("show_result")(show_result))
        workflow.add_node("integrate_web_search", time_node("integrate_web_search")(integrate_web_search))
        workflow.add_node("evaluate_vectorstore_quality", time_node("evaluate_vectorstore_quality")(evaluate_vectorstore_quality))
        workflow.add_node("generate_chart", time_node("generate_chart")(generate_comparison_chart))
        
        # START -> detect_alpha (FIRST check for ALPHA queries)
        workflow.add_edge(START, "detect_alpha")
        
        # ALPHA routing: detect_alpha -> alpha_retrieve OR preprocess
        workflow.add_conditional_edges(
            "detect_alpha",
            route_alpha_workflow,
           {
                "alpha": "alpha_retrieve",
                "normal": "preprocess"
            },
        )
        
        # ALPHA workflow: retrieve -> generate -> show_result -> END
        workflow.add_edge("alpha_retrieve", "alpha_generate")
        workflow.add_edge("alpha_generate", "show_result")
        
        # Preprocess -> Router (Vectorstore vs WebSearch)
        workflow.add_conditional_edges(
            "preprocess",
            route_question,
            {
                "vectorstore": "retrieve",
                "web_search": "web_search",
                "generate": "generate"
            },
        )

        # Retrieve now gets both text and images in one call - go directly to grading
        workflow.add_edge("retrieve", "grade_documents")

        workflow.add_edge("web_search", "generate")
        
        # Memory-only queries go directly to show_result (instant response!)
        # workflow.add_edge("memory_only", "show_result")  <-- REMOVED: Node does not exist

        workflow.add_conditional_edges(
            "grade_documents",
            decide_to_generate,
            {
                "financial_web_search": "financial_web_search",
                "generate": "generate",  # Changed: go directly to generate now
                "integrate_web_search": "integrate_web_search",
            },
        )

        # New edge for web integration
        workflow.add_conditional_edges(
            "integrate_web_search",
            decide_after_web_integration,
            {
                "grade_documents": "grade_documents",
                "financial_web_search": "financial_web_search",
            },
        )

        workflow.add_edge("financial_web_search", "generate")
        
        # Transform query goes directly back to retrieve (no more cross-reference analysis)
        workflow.add_edge("transform_query", "retrieve")

        workflow.add_conditional_edges(
            "generate",
            grade_generation_v_documents_and_question,
            {
                "not supported": "generate",
                "useful": "decide_chart",  # Changed: go to chart decision node
                "not useful": "transform_query",
            },
        )
        
        # NEW: Add a decision node that routes to either chart generation or show_result
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
            print("Graph compiled successfully (WITH Checkpointer/Memory)")
        else:
            app = workflow.compile()
            print("Graph compiled successfully (context-free mode)")
        
        return app
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
            "tool_calls": [],
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
        
        print("\n" + "="*50)
        print("FINAL RESULT:")
        print("="*50)
        print(messages["messages"][-1].content)
        
    finally:
        # Always cleanup
        await graph_obj.cleanup()

if __name__ == '__main__':
    asyncio.run(main())
    