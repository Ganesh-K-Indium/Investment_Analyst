"""
Chat History Service
Manages CRUD operations for chat sessions and messages across RAG and Quant agents
"""
from sqlalchemy.orm import Session
from app.database.models import ChatSession, ChatMessage, Portfolio, AgentType, MessageRole
from typing import List, Optional, Dict, Any
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import json


class ChatService:
    """Business logic for chat history operations"""
    @staticmethod
    def get_session_summary(
        db: Session,
        session_id: str
    ) -> Optional[str]:
        """Get cached summary from database"""
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        return chat_session.summary if chat_session else None

    @staticmethod
    def get_user_summaries_by_agent(
        db: Session,
        user_id: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get all cached summaries for a user grouped by agent type.

        Args:
            db: Database session
            user_id: User identifier

        Returns:
            Dictionary with 'rag' and 'quant' keys, each containing list of summaries
        """
        rag_sessions = db.query(ChatSession).filter(
            ChatSession.user_id == user_id,
            ChatSession.agent_type == AgentType.RAG,
            ChatSession.is_active == True,
            ChatSession.summary.isnot(None)
        ).order_by(ChatSession.last_message_at.desc()).all()

        quant_sessions = db.query(ChatSession).filter(
            ChatSession.user_id == user_id,
            ChatSession.agent_type == AgentType.QUANT,
            ChatSession.is_active == True,
            ChatSession.summary.isnot(None)
        ).order_by(ChatSession.last_message_at.desc()).all()

        rag_summaries = [
            {
                "session_id": session.session_id,
                "title": session.title,
                "summary": session.summary,
                "summary_updated_at": session.summary_updated_at.isoformat() if session.summary_updated_at else None,
                "message_count": len(session.messages) if session.messages else 0,
                "created_at": session.created_at.isoformat(),
                "last_message_at": session.last_message_at.isoformat() if session.last_message_at else None
            }
            for session in rag_sessions
        ]

        quant_summaries = [
            {
                "session_id": session.session_id,
                "title": session.title,
                "summary": session.summary,
                "summary_updated_at": session.summary_updated_at.isoformat() if session.summary_updated_at else None,
                "message_count": len(session.messages) if session.messages else 0,
                "created_at": session.created_at.isoformat(),
                "last_message_at": session.last_message_at.isoformat() if session.last_message_at else None
            }
            for session in quant_sessions
        ]

        return {
            "rag": rag_summaries,
            "quant": quant_summaries
        }

    # @staticmethod
    # def generate_chat_summary(
    #     db: Session,
    #     session_id: str,
    #     max_messages: Optional[int] = 50,
    #     llm_model: str = "gpt-4o-mini",
    #     store_in_db: bool = True
    # ) -> Optional[str]:
    #     """
    #     Generate LLM summary of chat session history.
        
    #     Args:
    #         db: Database session
    #         session_id: Session identifier
    #         max_messages: Maximum recent messages to summarize (default: 50)
    #         llm_model: LLM model to use for summarization
            
    #     Returns:
    #         Summary text or None if session not found
    #     """
    #     from langchain_openai import ChatOpenAI
    #     from langchain_core.prompts  import ChatPromptTemplate
    #     # from langchain.prompts import ChatPromptTemplate
    #     from langchain_core.output_parsers import StrOutputParser
        
    #     # Get chat session and recent messages
    #     chat_session = db.query(ChatSession).filter(
    #         ChatSession.session_id == session_id
    #     ).first()
        
    #     if not chat_session:
    #         return None
        
    #     messages = ChatService.get_session_messages(
    #         db=db,
    #         session_id=session_id,
    #         limit=max_messages
    #     )
        
    #     if not messages:
    #         return "No messages in this chat session."
        
    #     # Format conversation for LLM
    #     conversation = []
    #     for msg in reversed(messages):  # Most recent first for better context
    #         role = "Human" if msg.role == "user" else "Assistant"
    #         content_preview = msg.content[:200] + "..." if len(msg.content) > 200 else msg.content
    #         conversation.append(f"{role}: {content_preview}")
        
    #     conversation_text = "\n\n".join(conversation[-20:])  # Last 20 exchanges max
        
    #     # LLM summarization chain
    #     llm = ChatOpenAI(model=llm_model, temperature=0.1)
    #     prompt = ChatPromptTemplate.from_template("""
    #     Summarize the key topics, questions asked, and main insights from this investment analysis conversation.
    #     Focus on portfolio analysis, stock insights, document findings, and actionable takeaways.
    #     Also ensure that you keep a seperate section of all the chart urls that maybe found.
                                            
        
    #     Keep summary concise (2-4 sentences) but comprehensive. Use bullet points for clarity.
        
    #     Conversation:
    #     {conversation}
        
    #     Summary:""")
        
    #     chain = prompt | llm | StrOutputParser()
        
    #     try:
    #         summary = chain.invoke({"conversation": conversation_text})
    #         return summary.strip()
    #         # Store in database if requested
    #         if store_in_db:
    #             chat_session = db.query(ChatSession).filter(
    #                 ChatSession.session_id == session_id
    #             ).first()
                
    #             if chat_session:
    #                 chat_session.summary = summary
    #                 chat_session.summary_updated_at = datetime.utcnow()
    #                 db.commit()
    #                 db.refresh(chat_session)
    #         return summary
    #     except Exception as e:
    #         return f"Summary generation failed: {str(e)}"

    

    # ... inside your class ...

    @staticmethod
    def generate_chat_summary(
        db: Session,
        session_id: str,
        max_messages: Optional[int] = 50,
        llm_model: str = "gpt-4o-mini",
        store_in_db: bool = True
    ) -> Optional[str]:
        
        # 1. Get chat session
        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()
        
        if not chat_session:
            return None
        
        messages = ChatService.get_session_messages(
            db=db,
            session_id=session_id,
            limit=max_messages
        )
        
        if not messages:
            return "No messages in this chat session."
        
        # 2. Format conversation (reversed to chronological for the LLM)
        conversation = []
        for msg in reversed(messages):
            role = "Human" if msg.role == "user" else "Assistant"
            content = msg.content

            # Include metadata chart URLs (for compare chats where chart_url is in metadata)
            if msg.message_metadata:
                metadata = msg.message_metadata if isinstance(msg.message_metadata, dict) else {}
                if metadata.get("chart_url"):
                    content += f"\n[Chart URL: {metadata['chart_url']}]"
                if metadata.get("chart_filename"):
                    content += f"\n[Chart File: {metadata['chart_filename']}]"

            conversation.append(f"{role}: {content}")

        conversation_text = "\n\n".join(conversation[-20:]) 
        
        # 3. LLM Setup
        llm = ChatOpenAI(model=llm_model, temperature=0.1)
        prompt = ChatPromptTemplate.from_template("""
        Summarize the key topics, questions asked, and main insights from this investment analysis conversation.
        Focus on portfolio analysis, stock insights, and actionable takeaways.
        IMPORTANT: List all chart URLs found in the conversation in a separate section.
        
        Keep summary concise (2-4 sentences) but comprehensive. Use bullet points for clarity.
        
        Conversation:
        {conversation}
        
        Summary:""")
        
        chain = prompt | llm | StrOutputParser()
        
        try:
            summary = chain.invoke({"conversation": conversation_text}).strip()
            
            # 4. Storage Logic (Must happen BEFORE return)
            if store_in_db:
                chat_session.summary = summary
                chat_session.summary_updated_at = datetime.utcnow()
                db.commit()
                db.refresh(chat_session)
                
            return summary
            
        except Exception as e:
            # Consider logging the error here instead of just returning a string
            return f"Summary generation failed: {str(e)}"

    # ==================== Chat Session Management ====================
    
    @staticmethod
    def create_or_get_chat_session(
        db: Session,
        session_id: str,
        user_id: str,
        agent_type: AgentType,
        portfolio_id: Optional[int] = None,
        title: Optional[str] = None,
        session_metadata: Optional[Dict[str, Any]] = None
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
            session_metadata: Optional extra context e.g. {type, companies, portfolio_name}

        Returns:
            ChatSession object
        """
        # Check if session exists
        existing = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()

        if existing:
            # Update last_message_at and backfill session_metadata if not yet set
            existing.last_message_at = datetime.utcnow()
            if existing.session_metadata is None and session_metadata is not None:
                existing.session_metadata = session_metadata
            db.commit()
            db.refresh(existing)
            return existing

        # Create new session
        chat_session = ChatSession(
            session_id=session_id,
            user_id=user_id,
            portfolio_id=portfolio_id,
            agent_type=agent_type,
            title=title or f"{agent_type.value.upper()} Chat - {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}",
            session_metadata=session_metadata
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
            return -1  # Sentinel: session does not exist

        count = db.query(ChatMessage).filter(
            ChatMessage.chat_session_id == chat_session.id
        ).count()

        db.query(ChatMessage).filter(
            ChatMessage.chat_session_id == chat_session.id
        ).delete()

        db.commit()
        return count  # 0 means session existed but was already empty
    
    @staticmethod
    def delete_session(
        db: Session,
        session_id: str
    ) -> bool:
        """
        Permanently delete a session and all its messages.

        Also handles the case where a portfolio session was created (Session table)
        but no messages were ever sent, so no ChatSession record exists yet.

        Args:
            db: Database session
            session_id: Session identifier

        Returns:
            True if deleted, False if not found
        """
        from app.database.models import Session as PortfolioSession

        chat_session = db.query(ChatSession).filter(
            ChatSession.session_id == session_id
        ).first()

        if chat_session:
            # Messages are cascade-deleted via relationship
            db.delete(chat_session)
            db.commit()
            return True

        # No ChatSession found — the user may have created a portfolio session
        # (Session table) but never sent a message, so ChatSession was never created.
        portfolio_session = db.query(PortfolioSession).filter(
            PortfolioSession.id == session_id
        ).first()

        if portfolio_session:
            db.delete(portfolio_session)
            db.commit()
            return True

        return False
    
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
