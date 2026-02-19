"""
Chat History API endpoints
Manages chat sessions, history retrieval, export, and clearing across all agents
"""
from fastapi import APIRouter, HTTPException, Depends, Response
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
from app.database.connection import get_db_session
from app.services.chat import ChatService
from app.database.models import AgentType, ChatSession, ChatMessage
from datetime import datetime
import json

router = APIRouter(prefix="/chats", tags=["Chat History"])


# Pydantic Models
class ChatSessionResponse(BaseModel):
    session_id: str
    user_id: str
    agent_type: str
    portfolio_id: Optional[int]
    title: str
    is_active: bool
    message_count: int
    created_at: str
    last_message_at: Optional[str]


class ChatMessageResponse(BaseModel):
    role: str
    content: str
    metadata: Optional[Dict[str, Any]]
    timestamp: str


class ChatHistoryResponse(BaseModel):
    session_id: str
    user_id: str
    agent_type: str
    portfolio_id: Optional[int]
    title: str
    message_count: int
    messages: List[ChatMessageResponse]


class UpdateTitleRequest(BaseModel):
    title: str = Field(..., description="New title for the session")


class ChatStatsResponse(BaseModel):
    user_id: str
    total_sessions: int
    rag_sessions: int
    quant_sessions: int
    total_messages: int

# Add new Pydantic model
class ChatSummaryRequest(BaseModel):
    max_messages: Optional[int] = Field(50, ge=10, le=100, description="Max messages to summarize")
    llm_model: Optional[str] = Field("gpt-4o-mini", description="LLM model for summarization")

# Add this new endpoint (place after existing endpoints)
@router.post("/session/{session_id}/summary", response_model=str)
def generate_session_summary(
    session_id: str,
    request: ChatSummaryRequest,
    db: Session = Depends(get_db_session)
):
    """
    Generate LLM-powered summary of chat session.
    
    Request Body:
    - max_messages: Number of recent messages to include (10-100)
    - llm_model: LLM model to use (default: gpt-4o-mini)
    """
    try:
        summary = ChatService.generate_chat_summary(
            db=db,
            session_id=session_id,
            max_messages=request.max_messages,
            llm_model=request.llm_model
        )
        
        if not summary:
            raise HTTPException(status_code=404, detail="Chat session not found")
            
        return summary
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summary generation failed: {str(e)}")



@router.get("/user/{user_id}/sessions", response_model=List[ChatSessionResponse])
def get_user_chat_sessions(
    user_id: str,
    agent_type: Optional[str] = None,
    portfolio_id: Optional[int] = None,
    include_inactive: bool = False,
    db: Session = Depends(get_db_session)
):
    """
    Get all chat sessions for a user.
    
    Query Parameters:
    - agent_type: Filter by agent type (rag or quant)
    - portfolio_id: Filter by portfolio
    - include_inactive: Include inactive/archived sessions
    """
    try:
        # Parse agent type if provided
        agent_filter = None
        if agent_type:
            agent_filter = AgentType(agent_type.lower())
        
        sessions = ChatService.get_user_sessions(
            db=db,
            user_id=user_id,
            agent_type=agent_filter,
            portfolio_id=portfolio_id,
            include_inactive=include_inactive
        )
        
        # Build response with message counts
        result = []
        for session in sessions:
            message_count = len(session.messages)
            result.append(ChatSessionResponse(
                session_id=session.session_id,
                user_id=session.user_id,
                agent_type=session.agent_type.value,
                portfolio_id=session.portfolio_id,
                title=session.title,
                is_active=session.is_active,
                message_count=message_count,
                created_at=session.created_at.isoformat(),
                last_message_at=session.last_message_at.isoformat() if session.last_message_at else None
            ))
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}", response_model=ChatHistoryResponse)
def get_session_chat_history(
    session_id: str,
    limit: Optional[int] = None,
    offset: Optional[int] = 0,
    db: Session = Depends(get_db_session)
):
    """
    Get complete chat history for a session.
    
    Query Parameters:
    - limit: Maximum number of messages to return
    - offset: Skip first N messages (for pagination)
    """
    try:
        # Get session
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        
        # Get messages
        messages = ChatService.get_session_messages(
            db=db,
            session_id=session_id,
            limit=limit,
            offset=offset
        )
        
        # Build response
        return ChatHistoryResponse(
            session_id=chat_session.session_id,
            user_id=chat_session.user_id,
            agent_type=chat_session.agent_type.value,
            portfolio_id=chat_session.portfolio_id,
            title=chat_session.title,
            message_count=len(messages),
            messages=[
                ChatMessageResponse(
                    role=msg.role.value,
                    content=msg.content,
                    metadata=msg.message_metadata,
                    timestamp=msg.created_at.isoformat()
                )
                for msg in messages
            ]
        )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}/export")
def export_session(
    session_id: str,
    format: str = "json",
    db: Session = Depends(get_db_session)
):
    """
    Export chat session to JSON or TXT format.
    
    Query Parameters:
    - format: Export format (json or txt)
    """
    try:
        export_data = ChatService.export_session(db, session_id)
        
        if not export_data:
            raise HTTPException(status_code=404, detail="Chat session not found")
        
        if format.lower() == "txt":
            # Generate text format
            lines = [
                f"Chat Session Export",
                f"=" * 80,
                f"Session ID: {export_data['session_id']}",
                f"User: {export_data['user_id']}",
                f"Agent: {export_data['agent_type'].upper()}",
                f"Title: {export_data['title']}",
                f"Created: {export_data['created_at']}",
                f"Messages: {export_data['message_count']}",
                f"=" * 80,
                ""
            ]
            
            if export_data['portfolio']:
                lines.append(f"Portfolio: {export_data['portfolio']['name']}")
                lines.append(f"Companies: {', '.join(export_data['portfolio']['companies'])}")
                lines.append("")
            
            for msg in export_data['messages']:
                lines.append(f"[{msg['timestamp']}] {msg['role'].upper()}:")
                lines.append(msg['content'])
                lines.append("")
            
            content = "\n".join(lines)
            return Response(
                content=content,
                media_type="text/plain",
                headers={
                    "Content-Disposition": f"attachment; filename=chat_{session_id}.txt"
                }
            )
        else:
            # Return JSON format
            return Response(
                content=json.dumps(export_data, indent=2),
                media_type="application/json",
                headers={
                    "Content-Disposition": f"attachment; filename=chat_{session_id}.json"
                }
            )
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/session/{session_id}/title")
def update_session_title(
    session_id: str,
    payload: UpdateTitleRequest,
    db: Session = Depends(get_db_session)
):
    """Update the title of a chat session"""
    try:
        chat_session = ChatService.update_session_title(
            db=db,
            session_id=session_id,
            title=payload.title
        )
        
        if not chat_session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        
        return {
            "message": "Session title updated successfully",
            "session_id": session_id,
            "title": chat_session.title
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/session/{session_id}/messages")
def clear_session_messages(
    session_id: str,
    db: Session = Depends(get_db_session)
):
    """
    Clear all messages from a session (keeps the session).
    Useful for starting fresh while maintaining session metadata.
    """
    try:
        count = ChatService.clear_session_messages(db, session_id)

        if count == -1:
            raise HTTPException(status_code=404, detail="Chat session not found")

        return {
            "message": "Session messages cleared successfully",
            "session_id": session_id,
            "messages_deleted": count
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/session/{session_id}")
def delete_session(
    session_id: str,
    db: Session = Depends(get_db_session)
):
    """
    Permanently delete a chat session and all its messages.
    This action cannot be undone.
    """
    try:
        success = ChatService.delete_session(db, session_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Chat session not found")
        
        return {
            "message": "Session deleted successfully",
            "session_id": session_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/session/{session_id}/deactivate")
def deactivate_session(
    session_id: str,
    db: Session = Depends(get_db_session)
):
    """
    Mark a session as inactive (soft delete).
    Session is hidden but can be recovered.
    """
    try:
        success = ChatService.deactivate_session(db, session_id)
        
        if not success:
            raise HTTPException(status_code=404, detail="Chat session not found")
        
        return {
            "message": "Session deactivated successfully",
            "session_id": session_id
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}/stats")
def get_session_stats(
    session_id: str,
    db: Session = Depends(get_db_session)
):
    """Get statistics for a chat session"""
    try:
        stats = ChatService.get_session_stats(db, session_id)
        
        if not stats:
            raise HTTPException(status_code=404, detail="Chat session not found")
        
        return stats
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/{user_id}/stats", response_model=ChatStatsResponse)
def get_user_stats(
    user_id: str,
    db: Session = Depends(get_db_session)
):
    """Get statistics for all user's chat sessions"""
    try:
        stats = ChatService.get_user_stats(db, user_id)
        return ChatStatsResponse(**stats)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/{portfolio_id}/sessions", response_model=List[ChatSessionResponse])
def get_portfolio_chat_sessions(
    portfolio_id: int,
    agent_type: Optional[str] = None,
    db: Session = Depends(get_db_session)
):
    """
    Get all chat sessions for a portfolio.
    
    Query Parameters:
    - agent_type: Filter by agent type (rag or quant)
    """
    try:
        # Parse agent type if provided
        agent_filter = None
        if agent_type:
            agent_filter = AgentType(agent_type.lower())
        
        sessions = ChatService.get_portfolio_sessions(
            db=db,
            portfolio_id=portfolio_id,
            agent_type=agent_filter
        )
        
        result = []
        for session in sessions:
            message_count = len(session.messages)
            result.append(ChatSessionResponse(
                session_id=session.session_id,
                user_id=session.user_id,
                agent_type=session.agent_type.value,
                portfolio_id=session.portfolio_id,
                title=session.title,
                is_active=session.is_active,
                message_count=message_count,
                created_at=session.created_at.isoformat(),
                last_message_at=session.last_message_at.isoformat() if session.last_message_at else None
            ))
        
        return result
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
