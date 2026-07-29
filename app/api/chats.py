"""
Chat History API endpoints
Manages chat sessions, history retrieval, export, and clearing across all agents
"""
from fastapi import APIRouter, HTTPException, Depends, Response, Query, status
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from sqlalchemy import select, update, delete as sa_delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database.connection import get_db_session
from app.services.chat import ChatService
from app.database.models import AgentType, ChatSession, ChatMessage, ConsolidatedSummary, Portfolio, User
from app.auth.deps import get_current_user, verify_user_id_matches, verify_owner
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
    session_metadata: Optional[Dict[str, Any]] = None


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


class SummaryItem(BaseModel):
    session_id: str
    title: str
    summary: str
    summary_updated_at: Optional[str]
    message_count: int
    created_at: str
    last_message_at: Optional[str]
    detected_type: Optional[str] = None   # set for consolidated summaries ("rag", "compare", "quant")


class SummariesByAgentResponse(BaseModel):
    rag: List[SummaryItem]
    quant: List[SummaryItem]


class CreateChatSessionRequest(BaseModel):
    session_id: str = Field(..., description="Unique session identifier (frontend-generated)")
    user_id: str = Field(..., description="User identifier")
    agent_type: str = Field(..., description="Agent type: 'rag' or 'quant'")
    portfolio_id: Optional[int] = Field(None, description="Optional portfolio to link the session to")
    title: Optional[str] = Field(None, description="Optional session title")


class ChatSummaryRequest(BaseModel):
    max_messages: Optional[int] = Field(50, ge=10, le=100, description="Max messages to summarize")
    llm_model: Optional[str] = Field("gpt-4o-mini", description="LLM model for summarization")

class ChatSummaryResponse(BaseModel):
    session_id: str
    summary: str
    summary_updated_at: str
    message_count: int


class ConsolidatedSummaryRequest(BaseModel):
    session_ids: List[str] = Field(..., min_length=1, description="List of session IDs to consolidate")
    max_messages_per_session: Optional[int] = Field(30, ge=5, le=100, description="Max messages per session")
    llm_model: Optional[str] = Field("gpt-4o-mini", description="LLM model for summarization")


class ConsolidatedSummaryResponse(BaseModel):
    session_ids: List[str]
    detected_type: str  # auto-detected from session_metadata: 'rag', 'compare', or 'quant'
    consolidated_summary: str
    sessions_included: int
    generated_at: str

@router.get("/session/{session_id}/summary", response_model=ChatSummaryResponse)
async def get_session_summary(
    session_id: str,
    agent_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get cached chat summary from database (fast, no LLM call).

    Use POST endpoint to generate/store new summary.
    """
    result = await db.execute(
        select(ChatSession)
        .where(ChatSession.session_id == session_id)
        .options(selectinload(ChatSession.messages))
    )
    chat_session = result.scalar_one_or_none()

    if not chat_session:
        raise HTTPException(
            status_code=404,
            detail="No summary available. Generate one using POST /chats/session/{session_id}/summary"
        )
    verify_owner(chat_session.user_id, current_user)

    summary = chat_session.summary

    if not summary:
        raise HTTPException(
            status_code=404,
            detail="No summary available. Generate one using POST /chats/session/{session_id}/summary"
        )

    return ChatSummaryResponse(
        session_id=session_id,
        summary=summary,
        summary_updated_at=chat_session.summary_updated_at.isoformat(),
        message_count=len(chat_session.messages) if chat_session.messages else 0
    )


# Add this new endpoint (place after existing endpoints)
@router.post("/session/{session_id}/summary", response_model=str)
async def generate_session_summary(
    session_id: str,
    request: ChatSummaryRequest,
    agent_type: Optional[str] = Query(None, description="Agent type (rag/quant)"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Generate LLM-powered summary of chat session.

    Request Body:
    - max_messages: Number of recent messages to include (10-100)
    - llm_model: LLM model to use (default: gpt-4o-mini)
    """
    result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
    chat_session = result.scalar_one_or_none()
    if not chat_session:
        raise HTTPException(status_code=404, detail="Chat session not found")
    verify_owner(chat_session.user_id, current_user)

    try:
        summary = await ChatService.generate_chat_summary(
            db=db,
            session_id=session_id,
            max_messages=request.max_messages,
            llm_model=request.llm_model,
            store_in_db=True
        )

        if not summary:
            raise HTTPException(status_code=404, detail="Chat session not found")

        return summary

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Summary generation failed: {str(e)}")


@router.post("/sessions/consolidated-summary", response_model=ConsolidatedSummaryResponse)
async def generate_consolidated_summary(
    request: ConsolidatedSummaryRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Generate a single consolidated summary across multiple chat sessions.

    Agent type (rag / compare / quant) is auto-detected from each session's
    agent_type enum and session_metadata.type — no need to pass it explicitly.

    Request Body:
    - session_ids: List of session/thread IDs to consolidate (min 1)
    - max_messages_per_session: Messages to include per session (5-100, default 30)
    - llm_model: LLM model to use (default: gpt-4o-mini)
    """
    # No explicit user_id field on this request — verify ownership of every
    # session_id supplied so a user can't pull another user's session into
    # their consolidated summary. Sessions that don't exist are left for
    # ChatService.generate_consolidated_summary to skip as usual.
    for sid in request.session_ids:
        sess_result = await db.execute(select(ChatSession).where(ChatSession.session_id == sid))
        sess = sess_result.scalar_one_or_none()
        if sess:
            verify_owner(sess.user_id, current_user)

    try:
        result = await ChatService.generate_consolidated_summary(
            db=db,
            session_ids=request.session_ids,
            max_messages_per_session=request.max_messages_per_session,
            llm_model=request.llm_model
        )

        if not result:
            raise HTTPException(
                status_code=404,
                detail="No valid sessions found for the provided IDs"
            )

        return ConsolidatedSummaryResponse(
            session_ids=request.session_ids,
            detected_type=result["detected_type"],
            consolidated_summary=result["summary"],
            sessions_included=len(request.session_ids),
            generated_at=datetime.utcnow().isoformat()
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Consolidated summary generation failed: {str(e)}")


@router.get("/user/{user_id}/summaries", response_model=SummariesByAgentResponse)
async def get_user_summaries(
    user_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get all cached summaries for a user grouped by agent type (rag and quant).
    Returns only sessions with summaries that are active.
    """
    verify_user_id_matches(user_id, current_user)
    try:
        summaries = await ChatService.get_user_summaries_by_agent(db, user_id)
        return SummariesByAgentResponse(**summaries)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/session/{session_id}/summary", status_code=status.HTTP_200_OK)
async def delete_session_summary(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Clear the summary from a single session or delete a consolidated summary row."""
    if session_id.startswith("consolidated-"):
        row_id = int(session_id.split("-", 1)[1])
        result = await db.execute(select(ConsolidatedSummary).where(ConsolidatedSummary.id == row_id))
        row = result.scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Consolidated summary not found")
        verify_owner(row.user_id, current_user)
        await db.delete(row)
    else:
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        session = result.scalar_one_or_none()
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        verify_owner(session.user_id, current_user)
        session.summary = None
        session.summary_updated_at = None
    await db.commit()
    return {"message": "Summary deleted"}


@router.delete("/user/{user_id}/summaries", status_code=status.HTTP_200_OK)
async def clear_all_user_summaries(
    user_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Clear all summaries for a user — nullifies ChatSession summaries and deletes consolidated rows."""
    verify_user_id_matches(user_id, current_user)
    await db.execute(
        update(ChatSession)
        .where(ChatSession.user_id == user_id, ChatSession.summary.isnot(None))
        .values(summary=None, summary_updated_at=None)
    )
    await db.execute(sa_delete(ConsolidatedSummary).where(ConsolidatedSummary.user_id == user_id))
    await db.commit()
    return {"message": "All summaries cleared"}


@router.post("/session")
async def create_chat_session(
    payload: CreateChatSessionRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Register a new chat session in the database before any messages are sent.
    Call this when the user opens a new chat so the session can be deleted
    even if no messages have been sent yet.
    """
    verify_user_id_matches(payload.user_id, current_user)
    try:
        agent_type_enum = AgentType(payload.agent_type.lower())
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid agent_type '{payload.agent_type}'. Must be 'rag' or 'quant'."
        )

    try:
        chat_session = await ChatService.create_or_get_chat_session(
            db=db,
            session_id=payload.session_id,
            user_id=payload.user_id,
            agent_type=agent_type_enum,
            portfolio_id=payload.portfolio_id,
            title=payload.title
        )
        return {
            "session_id": chat_session.session_id,
            "user_id": chat_session.user_id,
            "agent_type": chat_session.agent_type.value,
            "portfolio_id": chat_session.portfolio_id,
            "title": chat_session.title,
            "created_at": chat_session.created_at.isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/{user_id}/sessions", response_model=List[ChatSessionResponse])
async def get_user_chat_sessions(
    user_id: str,
    agent_type: Optional[str] = None,
    portfolio_id: Optional[int] = None,
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get all chat sessions for a user.

    Query Parameters:
    - agent_type: Filter by agent type (rag or quant)
    - portfolio_id: Filter by portfolio
    - include_inactive: Include inactive/archived sessions
    """
    verify_user_id_matches(user_id, current_user)
    try:
        # Parse agent type if provided
        agent_filter = None
        if agent_type:
            agent_filter = AgentType(agent_type.lower())

        sessions = await ChatService.get_user_sessions(
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
                last_message_at=session.last_message_at.isoformat() if session.last_message_at else None,
                session_metadata=session.session_metadata
            ))

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}", response_model=ChatHistoryResponse)
async def get_session_chat_history(
    session_id: str,
    limit: Optional[int] = None,
    offset: Optional[int] = 0,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get complete chat history for a session.

    Query Parameters:
    - limit: Maximum number of messages to return
    - offset: Skip first N messages (for pagination)
    """
    try:
        # Get session
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

        if not chat_session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        verify_owner(chat_session.user_id, current_user)

        # Get messages
        messages = await ChatService.get_session_messages(
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
async def export_session(
    session_id: str,
    format: str = "json",
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Export chat session to JSON or TXT format.

    Query Parameters:
    - format: Export format (json or txt)
    """
    try:
        export_data = await ChatService.export_session(db, session_id)

        if not export_data:
            raise HTTPException(status_code=404, detail="Chat session not found")
        verify_owner(export_data["user_id"], current_user)

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
async def update_session_title(
    session_id: str,
    payload: UpdateTitleRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Update the title of a chat session"""
    try:
        existing_result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        existing_session = existing_result.scalar_one_or_none()
        if not existing_session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        verify_owner(existing_session.user_id, current_user)

        chat_session = await ChatService.update_session_title(
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
async def clear_session_messages(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Clear all messages from a session (keeps the session).
    Useful for starting fresh while maintaining session metadata.
    """
    try:
        existing_result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        existing_session = existing_result.scalar_one_or_none()
        if not existing_session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        verify_owner(existing_session.user_id, current_user)

        count = await ChatService.clear_session_messages(db, session_id)

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
async def delete_session(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Permanently delete a chat session and all its messages.
    This action cannot be undone.
    """
    try:
        # Mirrors ChatService.delete_session's own lookup: a ChatSession may
        # not exist yet if no message was ever sent, in which case the
        # legacy portfolio Session row is the one holding ownership.
        from app.database.models import Session as PortfolioSession
        existing_result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        existing_session = existing_result.scalar_one_or_none()
        if existing_session:
            verify_owner(existing_session.user_id, current_user)
        else:
            portfolio_session_result = await db.execute(
                select(PortfolioSession).where(PortfolioSession.id == session_id)
            )
            portfolio_session = portfolio_session_result.scalar_one_or_none()
            if not portfolio_session:
                raise HTTPException(status_code=404, detail="Chat session not found")
            verify_owner(portfolio_session.user_id, current_user)

        success = await ChatService.delete_session(db, session_id)
        
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
async def deactivate_session(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Mark a session as inactive (soft delete).
    Session is hidden but can be recovered.
    """
    try:
        existing_result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        existing_session = existing_result.scalar_one_or_none()
        if not existing_session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        verify_owner(existing_session.user_id, current_user)

        success = await ChatService.deactivate_session(db, session_id)
        
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
async def get_session_stats(
    session_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get statistics for a chat session"""
    try:
        existing_result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        existing_session = existing_result.scalar_one_or_none()
        if not existing_session:
            raise HTTPException(status_code=404, detail="Chat session not found")
        verify_owner(existing_session.user_id, current_user)

        stats = await ChatService.get_session_stats(db, session_id)
        
        if not stats:
            raise HTTPException(status_code=404, detail="Chat session not found")
        
        return stats
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/user/{user_id}/stats", response_model=ChatStatsResponse)
async def get_user_stats(
    user_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """Get statistics for all user's chat sessions"""
    verify_user_id_matches(user_id, current_user)
    try:
        stats = await ChatService.get_user_stats(db, user_id)
        return ChatStatsResponse(**stats)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/portfolio/{portfolio_id}/sessions", response_model=List[ChatSessionResponse])
async def get_portfolio_chat_sessions(
    portfolio_id: int,
    agent_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get all chat sessions for a portfolio.

    Query Parameters:
    - agent_type: Filter by agent type (rag or quant)
    """
    portfolio_result = await db.execute(select(Portfolio).where(Portfolio.id == portfolio_id))
    portfolio = portfolio_result.scalar_one_or_none()
    if not portfolio:
        raise HTTPException(status_code=404, detail="Portfolio not found")
    verify_owner(portfolio.user_id, current_user)

    try:
        # Parse agent type if provided
        agent_filter = None
        if agent_type:
            agent_filter = AgentType(agent_type.lower())

        sessions = await ChatService.get_portfolio_sessions(
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
                last_message_at=session.last_message_at.isoformat() if session.last_message_at else None,
                session_metadata=session.session_metadata
            ))

        return result

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
