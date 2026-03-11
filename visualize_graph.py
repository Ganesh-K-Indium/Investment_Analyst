import asyncio
import os
from rag.graph.builder import BuildingGraph

async def main():
    # Initialize the graph builder
    builder = BuildingGraph()
    
    # Get the compiled graph
    app = await builder.get_graph()
    
    # Generate the Mermaid PNG
    print("Generating Mermaid PNG...")
    try:
        # draw_mermaid_png returns binary data
        png_data = app.get_graph().draw_mermaid_png()
        
        # Save to file
        output_path = "rag_graph.png"
        with open(output_path, "wb") as f:
            f.write(png_data)
        
        print(f"Successfully generated graph: {os.path.abspath(output_path)}")
    except Exception as e:
        print(f"Error generating PNG: {e}")
        print("\nAttempting to generate Mermaid markdown instead...")
        try:
            mermaid_graph = app.get_graph().draw_mermaid()
            with open("rag_graph.md", "w") as f:
                f.write(f"```mermaid\n{mermaid_graph}\n```")
            print("Successfully generated Mermaid markdown: rag_graph.md")
            print("You can paste the content of rag_graph.md into a Mermaid editor like https://mermaid.live")
        except Exception as e2:
            print(f"Failed to generate Mermaid markdown: {e2}")

if __name__ == "__main__":
    asyncio.run(main())
