import asyncio
from Graph.invoke_graph import BuildingGraph

async def generate_graph_png():
    print("Initializing BuildingGraph...")
    graph_obj = BuildingGraph()
    
    print("Getting graph...")
    # No checkpointer needed for drawing the structure
    app = await graph_obj.get_graph()
    
    print("Generating Mermaid PNG...")
    try:
        png_data = app.get_graph().draw_mermaid_png()
        output_file = "graph_output.png"
        with open(output_file, "wb") as f:
            f.write(png_data)
        print(f"Graph saved to {output_file}")
    except Exception as e:
        print(f"Error generating PNG: {e}")

if __name__ == "__main__":
    asyncio.run(generate_graph_png())
