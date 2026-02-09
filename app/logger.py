# file: logger.py
import os
import json
import datetime

def format_graph_output(data: dict) -> str:
    """Format RAG graph output into Markdown with clear headings."""
    lines = []
    
    # Main Answer
    if "answer" in data and isinstance(data["answer"], str):
        lines.append("## 📝 Final Answer")
        lines.append(data["answer"])
        lines.append("")
    
    # Thread ID
    if "thread_id" in data:
        lines.append("## 🔗 Thread ID")
        lines.append(f"`{data['thread_id']}`")
        lines.append("")
    
    # Intermediate Message
    if "intermediate_message" in data and data["intermediate_message"]:
        lines.append("## 🔄 Intermediate Message")
        lines.append(data["intermediate_message"])
        lines.append("")
    
    # Search Status
    lines.append("## 🔍 Search Status")
    lines.append(f"- **Vectorstore Searched:** {data.get('vectorstore_searched', False)}")
    lines.append(f"- **Web Searched:** {data.get('web_searched', False)}")
    lines.append(f"- **Vectorstore Quality:** {data.get('vectorstore_quality', 'none')}")
    lines.append(f"- **Needs Web Fallback:** {data.get('needs_web_fallback', False)}")
    lines.append(f"- **Retry Count:** {data.get('retry_count', 0)}")
    lines.append("")
    
    # Summary Strategy
    if "summary_strategy" in data:
        lines.append("## 📊 Summary Strategy")
        lines.append(f"`{data['summary_strategy']}`")
        lines.append("")
    
    # Tool Calls
    if "tool_calls" in data and data["tool_calls"]:
        lines.append("## 🛠️ Tool Calls")
        for i, call in enumerate(data["tool_calls"], 1):
            if isinstance(call, dict):
                tool_name = call.get('tool', 'Unknown')
                lines.append(f"### {i}. {tool_name}")
                if "input" in call:
                    lines.append(f"```json")
                    lines.append(json.dumps(call.get('input'), indent=2))
                    lines.append(f"```")
                if "output" in call:
                    lines.append(f"**Output:** {json.dumps(call.get('output'), indent=2)}")
            else:
                lines.append(f"{i}. `{call}`")
            lines.append("")
    
    # Messages
    if "messages" in data and data["messages"]:
        lines.append("## 💬 Messages")
        for i, msg in enumerate(data["messages"], 1):
            if isinstance(msg, dict):
                msg_type = msg.get("type", "unknown")
                content = msg.get("content", "")
                lines.append(f"### Message {i}: {msg_type}")
                lines.append(content[:500] + "..." if len(content) > 500 else content)
            else:
                lines.append(f"### Message {i}")
                lines.append(str(msg)[:500])
            lines.append("")
    
    # Documents
    if "documents" in data and data["documents"]:
        lines.append("## 📄 Retrieved Documents")
        lines.append(f"**Total Documents:** {len(data['documents'])}")
        lines.append("")
        for i, doc in enumerate(data["documents"], 1):
            lines.append(f"### Document {i}")
            if isinstance(doc, dict):
                # Metadata
                if doc.get("metadata"):
                    lines.append("**Metadata:**")
                    lines.append("```json")
                    lines.append(json.dumps(doc["metadata"], indent=2))
                    lines.append("```")
                # Content (truncated)
                if doc.get("content"):
                    content = doc["content"]
                    lines.append("**Content:**")
                    lines.append(content[:300] + "..." if len(content) > 300 else content)
            else:
                lines.append(str(doc)[:300])
            lines.append("")
    
    # Document Sources
    if "document_sources" in data and data["document_sources"]:
        lines.append("## 📚 Document Sources")
        for source_type, sources in data["document_sources"].items():
            lines.append(f"### {source_type}")
            lines.append(f"Count: {len(sources) if isinstance(sources, list) else 1}")
            lines.append("")
    
    # Citation Info
    if "citation_info" in data and data["citation_info"]:
        lines.append("## 📖 Citation Information")
        for i, citation in enumerate(data["citation_info"], 1):
            lines.append(f"{i}. {json.dumps(citation, indent=2)}")
        lines.append("")
    
    # Legacy format support (for backward compatibility)
    answer_data = data.get("answer", {})
    if isinstance(answer_data, dict):
        if "messages" in answer_data and not data.get("messages"):
            lines.append("## Legacy Messages")
            for i, msg in enumerate(answer_data["messages"], 1):
                if isinstance(msg, dict):
                    content = msg.get("content", "")
                    msg_type = msg.get("type", "unknown")
                    lines.append(f"### Message {i} ({msg_type})")
                    lines.append(content)
                else:
                    lines.append(f"- {msg}")
                lines.append("")

        if "Intermediate_message" in answer_data:
            lines.append("## Legacy Intermediate Message")
            lines.append(answer_data["Intermediate_message"])
            lines.append("")

    return "\n".join(lines)


def format_ingestion_output(data: dict) -> str:
    """Format ingestion response into Markdown with clear logs."""
    lines = []
    answer_data = data.get("answer", {})

    if "request" in answer_data:
        lines.append("## Request")
        lines.append(answer_data["request"])
        lines.append("")

    if "logs" in answer_data:
        lines.append("## Ingestion Logs")
        for i, log in enumerate(answer_data["logs"], 1):
            lines.append(f"{i}. {log}")
        lines.append("")

    lines.append("## File Information")
    lines.append(f"- Source: {answer_data.get('source')}")
    lines.append(f"- File Name: {answer_data.get('file_name')}")
    lines.append(f"- Space Key: {answer_data.get('space_key')}")
    lines.append(f"- Ticket ID: {answer_data.get('ticket_id')}")
    lines.append(f"- File URL: {answer_data.get('file_url')}")
    lines.append("")

    return "\n".join(lines)


def log_response(payload: dict, data: dict, folder: str = "responses") -> None:
    """Save formatted response to markdown log."""
    os.makedirs(folder, exist_ok=True)

    now = datetime.datetime.now()
    filename = now.strftime("%Y-%m-%d_%H-%M-%S") + ".md"
    filepath = os.path.join(folder, filename)

    # Check if it's an ingestion response (has logs) or RAG response
    if isinstance(data.get("answer"), dict) and "logs" in data.get("answer", {}):
        content = format_ingestion_output(data)
    else:
        content = format_graph_output(data)

    md_content = (
        "# API Response Report\n"
        + "="*50 + "\n"
        + f"Generated: {now.strftime('%Y-%m-%d %H:%M:%S')}\n"
        + f"Query: {payload.get('query', 'N/A')}\n"
        + f"Thread ID: {payload.get('thread_id', 'N/A')}\n"
        + "="*50 + "\n\n"
        + content
    )

    with open(filepath, "w", encoding="utf-8") as f:
        f.write(md_content)
    
    print(f"📝 Response logged to: {filepath}")
