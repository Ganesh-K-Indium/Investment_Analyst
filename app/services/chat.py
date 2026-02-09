"""
Chat History Service
Manages CRUD operations for chat sessions and messages across RAG and Quant agents
"""
from sqlalchemy.orm import Session
from app.database.models import ChatSession, ChatMessage, Portfolio, AgentType, MessageRole
from typing import List, Optional, Dict, Any
from datetime import datetime
import json


class ChatService:
    """Business logic for chat history operations"""
    
    # ==================== Chat Session Management ====================
    
    @staticmethod
    def create_or_get_chat_session(
        db: Session,
        session_id: str,
        user_id: str,
        agent_type: AgentType,
        portfolio_id: Optional[int] = None,
        title: Optional[str] = None
    ) -> ChatSession:
        """
        Create a new chat session or get existing one.
        
        Args:
            db: Database session
            session_id: Unique session identifier (thread_id)
            user_id: User identifier
            agent_type: Type of agent (rag or quant)
            portfolio_id: Optional portfolio ID
            title: Optional session title
            
        Returns:
            ChatSession object
        """
        # Check if session exists
        existing = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if existing:
            # Update last_message_at
            existing.last_message_at = datetime.utcnow()
            db.commit()
            db.refresh(existing)
            return existing
        
        # Create new session
        chat_session = ChatSession(
            session_id=session_id,
            user_id=user_id,
            portfolio_id=portfolio_id,
            agent_type=agent_type,
            title=title or f"{agent_type.value.upper()} Chat - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
        )
        db.add(chat_session)
        db.commit()
        db.refresh(chat_session)
        return chat_session
    
    @staticmethod
    def add_message(
        db: Session,
        session_id: str,
        role: MessageRole,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        token_count: Optional[int] = None
    ) -> ChatMessage:
        """
        Add a message to a chat session.
        
        Args:
            db: Database session
            session_id: Session identifier
            role: Message role (user, assistant, system)
            content: Message content
            metadata: Optional metadata (sources, citations, etc.)
            token_count: Optional token count
            
        Returns:
            ChatMessage object
        """
        # Get chat session
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            raise ValueError(f"Chat session {session_id} not found")
        
        # Create message - use message_metadata instead of metadata
        message = ChatMessage(
            chat_session_id=chat_session.id,
            role=role,
            content=content,
            message_metadata=metadata,
            token_count=token_count
        )
        db.add(message)
        
        # Update session's last_message_at
        chat_session.last_message_at = datetime.utcnow()
        
        db.commit()
        db.refresh(message)
        return message
    
    @staticmethod
    def get_session_messages(
        db: Session,
        session_id: str,
        limit: Optional[int] = None,
        offset: Optional[int] = 0
    ) -> List[ChatMessage]:
        """
        Get messages for a chat session.
        
        Args:
            db: Database session
            session_id: Session identifier
            limit: Optional limit on number of messages
            offset: Optional offset for pagination
            
        Returns:
            List of ChatMessage objects ordered by created_at
        """
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            return []
        
        query = db.query(ChatMessage).filter(
            ChatMessage.chat_session_id == chat_session.id
        ).order_by(ChatMessage.created_at.asc())
        
        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)
        
        return query.all()
    
    @staticmethod
    def get_user_sessions(
        db: Session,
        user_id: str,
        agent_type: Optional[AgentType] = None,
        portfolio_id: Optional[int] = None,
        include_inactive: bool = False
    ) -> List[ChatSession]:
        """
        Get all chat sessions for a user.
        
        Args:
            db: Database session
            user_id: User identifier
            agent_type: Optional filter by agent type
            portfolio_id: Optional filter by portfolio
            include_inactive: Include inactive sessions
            
        Returns:
            List of ChatSession objects ordered by last_message_at desc
        """
        query = db.query(ChatSession).filter(ChatSession.user_id == user_id)
        
        if agent_type:
            query = query.filter(ChatSession.agent_type == agent_type)
        
        if portfolio_id:
            query = query.filter(ChatSession.portfolio_id == portfolio_id)
        
        if not include_inactive:
            query = query.filter(ChatSession.is_active == True)
        
        return query.order_by(ChatSession.last_message_at.desc()).all()
    
    @staticmethod
    def get_portfolio_sessions(
        db: Session,
        portfolio_id: int,
        agent_type: Optional[AgentType] = None
    ) -> List[ChatSession]:
        """
        Get all chat sessions for a portfolio.
        
        Args:
            db: Database session
            portfolio_id: Portfolio identifier
            agent_type: Optional filter by agent type
            
        Returns:
            List of ChatSession objects
        """
        query = db.query(ChatSession).filter(
            ChatSession.portfolio_id == portfolio_id,
            ChatSession.is_active == True
        )
        
        if agent_type:
            query = query.filter(ChatSession.agent_type == agent_type)
        
        return query.order_by(ChatSession.last_message_at.desc()).all()
    
    @staticmethod
    def update_session_title(
        db: Session,
        session_id: str,
        title: str
    ) -> Optional[ChatSession]:
        """Update session title"""
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            return None
        
        chat_session.title = title
        db.commit()
        db.refresh(chat_session)
        return chat_session
    
    @staticmethod
    def deactivate_session(
        db: Session,
        session_id: str
    ) -> bool:
        """Mark a session as inactive (soft delete)"""
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            return False
        
        chat_session.is_active = False
        db.commit()
        return True
    
    @staticmethod
    def clear_session_messages(
        db: Session,
        session_id: str
    ) -> int:
        """
        Clear all messages from a session.
        
        Args:
            db: Database session
            session_id: Session identifier
            
        Returns:
            Number of messages deleted
        """
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            return 0
        
        count = db.query(ChatMessage).filter(
            ChatMessage.chat_session_id == chat_session.id
        ).count()
        
        db.query(ChatMessage).filter(
            ChatMessage.chat_session_id == chat_session.id
        ).delete()
        
        db.commit()
        return count
    
    @staticmethod
    def delete_session(
        db: Session,
        session_id: str
    ) -> bool:
        """
        Permanently delete a session and all its messages.
        
        Args:
            db: Database session
            session_id: Session identifier
            
        Returns:
            True if deleted, False if not found
        """
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            return False
        
        # Messages will be cascade deleted
        db.delete(chat_session)
        db.commit()
        return True
    
    @staticmethod
    def export_session(
        db: Session,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Export a complete chat session with all messages.
        
        Args:
            db: Database session
            session_id: Session identifier
            
        Returns:
            Dictionary with session and messages data
        """
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            return None
        
        messages = db.query(ChatMessage).filter(
            ChatMessage.chat_session_id == chat_session.id
        ).order_by(ChatMessage.created_at.asc()).all()
        
        # Get portfolio info if linked
        portfolio_info = None
        if chat_session.portfolio_id:
            portfolio = db.query(Portfolio).filter(
                Portfolio.id == chat_session.portfolio_id
            ).first()
            if portfolio:
                portfolio_info = {
                    "id": portfolio.id,
                    "name": portfolio.name,
                    "companies": portfolio.company_names
                }
        
        return {
            "session_id": chat_session.session_id,
            "user_id": chat_session.user_id,
            "agent_type": chat_session.agent_type.value,
            "title": chat_session.title,
            "portfolio": portfolio_info,
            "created_at": chat_session.created_at.isoformat(),
            "last_message_at": chat_session.last_message_at.isoformat() if chat_session.last_message_at else None,
            "message_count": len(messages),
            "messages": [
                {
                    "role": msg.role.value,
                    "content": msg.content,
                    "metadata": msg.message_metadata,
                    "token_count": msg.token_count,
                    "timestamp": msg.created_at.isoformat()
                }
                for msg in messages
            ]
        }
    
    @staticmethod
    def get_session_stats(
        db: Session,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get statistics for a chat session"""
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            return None
        
        message_count = db.query(ChatMessage).filter(
            ChatMessage.chat_session_id == chat_session.id
        ).count()
        
        total_tokens = db.query(
            db.func.sum(ChatMessage.token_count)
        ).filter(
            ChatMessage.chat_session_id == chat_session.id
        ).scalar() or 0
        
        return {
            "session_id": chat_session.session_id,
            "message_count": message_count,
            "total_tokens": total_tokens,
            "agent_type": chat_session.agent_type.value,
            "created_at": chat_session.created_at.isoformat(),
            "last_message_at": chat_session.last_message_at.isoformat() if chat_session.last_message_at else None
        }
    
    @staticmethod
    def get_user_stats(
        db: Session,
        user_id: str
    ) -> Dict[str, Any]:
        """Get statistics for all user's chat sessions"""
        total_sessions = db.query(ChatSession).filter(
            ChatSession.user_id == user_id
        ).count()
        
        rag_sessions = db.query(ChatSession).filter(
            ChatSession.user_id == user_id,
            ChatSession.agent_type == AgentType.RAG
        ).count()
        
        quant_sessions = db.query(ChatSession).filter(
            ChatSession.user_id == user_id,
            ChatSession.agent_type == AgentType.QUANT
        ).count()
        
        total_messages = db.query(ChatMessage).join(ChatSession).filter(
            ChatSession.user_id == user_id
        ).count()
        
        return {
            "user_id": user_id,
            "total_sessions": total_sessions,
            "rag_sessions": rag_sessions,
            "quant_sessions": quant_sessions,
            "total_messages": total_messages
        }
