"""
Chat History Service
Manages CRUD operations for chat sessions and messages across RAG and Quant agents
"""
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.database.models import ChatSession, ChatMessage, Portfolio, AgentType, MessageRole, ConsolidatedSummary
from typing import List, Optional, Dict, Any
from datetime import datetime
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import StrOutputParser
import json


class ChatService:
    """Business logic for chat history operations"""
    @staticmethod
    async def get_session_summary(
        db: AsyncSession,
        session_id: str
    ) -> Optional[str]:
        """Get cached summary from database"""
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()
        return chat_session.summary if chat_session else None

    @staticmethod
    async def get_user_summaries_by_agent(
        db: AsyncSession,
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
        rag_result = await db.execute(
            select(ChatSession)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.agent_type == AgentType.RAG,
                ChatSession.is_active == True,
                ChatSession.summary.isnot(None)
            )
            .options(selectinload(ChatSession.messages))
            .order_by(ChatSession.last_message_at.desc())
        )
        rag_sessions = rag_result.scalars().all()

        quant_result = await db.execute(
            select(ChatSession)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.agent_type == AgentType.QUANT,
                ChatSession.is_active == True,
                ChatSession.summary.isnot(None)
            )
            .options(selectinload(ChatSession.messages))
            .order_by(ChatSession.last_message_at.desc())
        )
        quant_sessions = quant_result.scalars().all()

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

        consolidated_result = await db.execute(
            select(ConsolidatedSummary)
            .where(ConsolidatedSummary.user_id == user_id)
            .order_by(ConsolidatedSummary.created_at.desc())
        )
        consolidated_rows = consolidated_result.scalars().all()

        for row in consolidated_rows:
            item = {
                "session_id": f"consolidated-{row.id}",
                "title": row.title or "Consolidated Summary",
                "summary": row.summary,
                "summary_updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "message_count": row.sessions_included,
                "created_at": row.created_at.isoformat(),
                "last_message_at": None,
                "detected_type": row.detected_type,  # "rag", "compare", or "quant"
            }
            if row.detected_type == "quant":
                quant_summaries.append(item)
            else:
                rag_summaries.append(item)

        return {
            "rag": rag_summaries,
            "quant": quant_summaries,
        }

    @staticmethod
    async def generate_chat_summary(
        db: AsyncSession,
        session_id: str,
        max_messages: Optional[int] = 50,
        llm_model: str = "gpt-4o-mini",
        store_in_db: bool = True
    ) -> Optional[str]:

        # 1. Get chat session
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

        if not chat_session:
            return None

        messages = await ChatService.get_session_messages(
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
            Generate a structured report summarizing this investment analysis conversation.

            Focus on portfolio analysis, stock insights, actionable takeaways, and all relevant context.

            IMPORTANT:
            - List all chart URLs found in the conversation in a separate section.
            - In key topics, include comprehensive context from the questions (e.g., if questions mention timelines like 30 & 60 days chart plottings, explicitly note "Timeline: 30-day and 60-day chart generation" within the paragraph).

            Structure the report exactly as follows:

            ## Key Topics
            A single comprehensive paragraph (3-5 sentences) covering ONLY the main topics and context extracted directly from the questions asked, including timelines, stocks, metrics, and analysis requests.
            ## Main Insights
            Comprehensive summary of key portfolio/stock takeaways and detailed actionable recommendations.

            ## Charts
            - List all chart URLs verbatim.

            ## Questions Asked
            - Raw exact text of each question from the conversation, one per bullet.

            Conversation:
            {conversation}

            Report:
        """)

        chain = prompt | llm | StrOutputParser()

        try:
            summary = (await chain.ainvoke({"conversation": conversation_text})).strip()

            # 4. Storage Logic (Must happen BEFORE return)
            if store_in_db:
                chat_session.summary = summary
                chat_session.summary_updated_at = datetime.utcnow()
                await db.commit()
                await db.refresh(chat_session)

            return summary

        except Exception as e:
            # Consider logging the error here instead of just returning a string
            return f"Summary generation failed: {str(e)}"

    @staticmethod
    async def generate_consolidated_summary(
        db: AsyncSession,
        session_ids: List[str],
        max_messages_per_session: int = 30,
        llm_model: str = "gpt-4o-mini"
    ) -> Optional[Dict[str, Any]]:
        """
        Generate a single consolidated summary across multiple chat sessions.

        Agent type and focus are auto-detected from each session's agent_type +
        session_metadata.type:
          - AgentType.QUANT                          → "quant"
          - AgentType.RAG + metadata.type="compare"  → "compare"
          - AgentType.RAG + metadata.type="ask"      → "rag"

        Args:
            db: Database session
            session_ids: List of session identifiers to consolidate
            max_messages_per_session: Max messages to pull per session
            llm_model: LLM model for summarization

        Returns:
            Dict {"summary": str, "detected_type": str}, or None if no valid sessions
        """
        sessions_data = []

        for session_id in session_ids:
            result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
            chat_session = result.scalar_one_or_none()

            if not chat_session:
                continue

            messages = await ChatService.get_session_messages(
                db=db,
                session_id=session_id,
                limit=max_messages_per_session
            )

            if not messages:
                continue

            # Detect session type from metadata first, then agent_type enum
            meta = chat_session.session_metadata or {}
            meta_type = meta.get("type", "").lower()  # "ask", "compare", or ""
            if chat_session.agent_type == AgentType.QUANT:
                session_type = "quant"
            elif meta_type == "compare":
                session_type = "compare"
            else:
                session_type = "rag"

            conversation = []
            for msg in reversed(messages):
                role = "Human" if msg.role == "user" else "Assistant"
                content = msg.content
                if msg.message_metadata:
                    metadata = msg.message_metadata if isinstance(msg.message_metadata, dict) else {}
                    if metadata.get("chart_url"):
                        content += f"\n[Chart URL: {metadata['chart_url']}]"
                    if metadata.get("chart_filename"):
                        content += f"\n[Chart File: {metadata['chart_filename']}]"
                conversation.append(f"{role}: {content}")

            sessions_data.append({
                "session_id": session_id,
                "title": chat_session.title or session_id,
                "session_type": session_type,
                "conversation": "\n\n".join(conversation[-15:])
            })

        if not sessions_data:
            return None

        # Determine dominant type by majority across sessions
        type_counts: Dict[str, int] = {}
        for s in sessions_data:
            type_counts[s["session_type"]] = type_counts.get(s["session_type"], 0) + 1
        detected_type = max(type_counts, key=lambda t: type_counts[t])

        # Build combined sessions block
        sessions_text = ""
        for i, session in enumerate(sessions_data, 1):
            sessions_text += f"\n\n### Session {i}: {session['title']}\n"
            sessions_text += session["conversation"]

        if detected_type == "compare":
            focus = "comparative stock/company analysis, highlighting differences, similarities, and relative metrics across entities"
        elif detected_type == "quant":
            focus = "quantitative analysis, technical indicators, chart patterns, and numerical metrics"
        else:
            focus = "document-based research, portfolio analysis, and knowledge base insights"

        llm = ChatOpenAI(model=llm_model, temperature=0.1)
        prompt = ChatPromptTemplate.from_template("""
            Generate a consolidated investment analysis report from multiple chat sessions.
            Focus on: {focus}

            IMPORTANT:
            - Synthesize insights across all sessions, not just list them separately.
            - List all chart URLs found across all sessions in a dedicated section.
            - Note recurring themes, conflicting findings, and overall conclusions.

            Structure the report exactly as follows:

            ## Overview
            A single paragraph summarizing the combined scope and purpose across all sessions.

            ## Key Topics (Across All Sessions)
            A comprehensive paragraph covering all major topics, timelines, stocks/assets, and analysis requests found across sessions.

            ## Consolidated Insights
            Synthesized takeaways combining findings from all sessions — patterns, trends, and actionable recommendations.

            ## Charts
            - List all chart URLs verbatim from all sessions.

            ## Questions Asked (All Sessions)
            - All unique questions from all sessions, one per bullet.

            Sessions:
            {sessions_text}

            Consolidated Report:
        """)

        chain = prompt | llm | StrOutputParser()

        try:
            summary = (await chain.ainvoke({"focus": focus, "sessions_text": sessions_text})).strip()

            # Derive user_id and title from first session
            user_id = sessions_data[0]["session_id"]  # fallback
            first_result = await db.execute(
                select(ChatSession).where(ChatSession.session_id == sessions_data[0]["session_id"])
            )
            first_session = first_result.scalar_one_or_none()
            if first_session:
                user_id = first_session.user_id
            title = "Consolidated: " + ", ".join(s["title"] for s in sessions_data[:3])
            if len(sessions_data) > 3:
                title += f" (+{len(sessions_data) - 3} more)"

            # Store in DB
            consolidated = ConsolidatedSummary(
                user_id=user_id,
                session_ids=session_ids,
                detected_type=detected_type,
                title=title,
                summary=summary,
                sessions_included=len(sessions_data),
            )
            db.add(consolidated)
            await db.commit()
            await db.refresh(consolidated)

            return {"summary": summary, "detected_type": detected_type, "id": consolidated.id}
        except Exception as e:
            return {"summary": f"Consolidated summary generation failed: {str(e)}", "detected_type": detected_type}

    # ==================== Chat Session Management ====================

    @staticmethod
    async def create_or_get_chat_session(
        db: AsyncSession,
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
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        existing = result.scalar_one_or_none()

        if existing:
            # Update last_message_at
            existing.last_message_at = datetime.utcnow()
            # Correct agent_type if it was pre-created with the wrong type
            # (e.g. POST /portfolios/sessions always used to default to RAG)
            if existing.agent_type != agent_type:
                existing.agent_type = agent_type
                if title:
                    existing.title = title
            # Backfill session_metadata if not yet set
            if existing.session_metadata is None and session_metadata is not None:
                existing.session_metadata = session_metadata
            await db.commit()
            await db.refresh(existing)
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
        await db.commit()
        await db.refresh(chat_session)
        return chat_session

    @staticmethod
    async def add_message(
        db: AsyncSession,
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
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

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

        await db.commit()
        await db.refresh(message)
        return message

    @staticmethod
    async def get_session_messages(
        db: AsyncSession,
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
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

        if not chat_session:
            return []

        query = select(ChatMessage).where(
            ChatMessage.chat_session_id == chat_session.id
        ).order_by(ChatMessage.created_at.asc())

        if offset:
            query = query.offset(offset)
        if limit:
            query = query.limit(limit)

        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_user_sessions(
        db: AsyncSession,
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
        query = select(ChatSession).where(ChatSession.user_id == user_id).options(selectinload(ChatSession.messages))

        if agent_type:
            query = query.where(ChatSession.agent_type == agent_type)

        if portfolio_id:
            query = query.where(ChatSession.portfolio_id == portfolio_id)

        if not include_inactive:
            query = query.where(ChatSession.is_active == True)

        query = query.order_by(ChatSession.last_message_at.desc())

        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def get_portfolio_sessions(
        db: AsyncSession,
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
        query = select(ChatSession).where(
            ChatSession.portfolio_id == portfolio_id,
            ChatSession.is_active == True
        ).options(selectinload(ChatSession.messages))

        if agent_type:
            query = query.where(ChatSession.agent_type == agent_type)

        query = query.order_by(ChatSession.last_message_at.desc())

        result = await db.execute(query)
        return list(result.scalars().all())

    @staticmethod
    async def update_session_title(
        db: AsyncSession,
        session_id: str,
        title: str
    ) -> Optional[ChatSession]:
        """Update session title"""
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

        if not chat_session:
            return None

        chat_session.title = title
        await db.commit()
        await db.refresh(chat_session)
        return chat_session

    @staticmethod
    async def deactivate_session(
        db: AsyncSession,
        session_id: str
    ) -> bool:
        """Mark a session as inactive (soft delete)"""
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

        if not chat_session:
            return False

        chat_session.is_active = False
        await db.commit()
        return True

    @staticmethod
    async def clear_session_messages(
        db: AsyncSession,
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
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

        if not chat_session:
            return -1  # Sentinel: session does not exist

        count_result = await db.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.chat_session_id == chat_session.id)
        )
        count = count_result.scalar_one()

        await db.execute(delete(ChatMessage).where(ChatMessage.chat_session_id == chat_session.id))

        await db.commit()
        return count  # 0 means session existed but was already empty

    @staticmethod
    async def delete_session(
        db: AsyncSession,
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

        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

        if chat_session:
            # Messages are cascade-deleted via relationship
            await db.delete(chat_session)
            await db.commit()
            return True

        # No ChatSession found — the user may have created a portfolio session
        # (Session table) but never sent a message, so ChatSession was never created.
        portfolio_result = await db.execute(select(PortfolioSession).where(PortfolioSession.id == session_id))
        portfolio_session = portfolio_result.scalar_one_or_none()

        if portfolio_session:
            await db.delete(portfolio_session)
            await db.commit()
            return True

        return False

    @staticmethod
    async def export_session(
        db: AsyncSession,
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
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

        if not chat_session:
            return None

        messages_result = await db.execute(
            select(ChatMessage)
            .where(ChatMessage.chat_session_id == chat_session.id)
            .order_by(ChatMessage.created_at.asc())
        )
        messages = list(messages_result.scalars().all())

        # Get portfolio info if linked
        portfolio_info = None
        if chat_session.portfolio_id:
            portfolio_result = await db.execute(select(Portfolio).where(Portfolio.id == chat_session.portfolio_id))
            portfolio = portfolio_result.scalar_one_or_none()
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
    async def get_session_stats(
        db: AsyncSession,
        session_id: str
    ) -> Optional[Dict[str, Any]]:
        """Get statistics for a chat session"""
        result = await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))
        chat_session = result.scalar_one_or_none()

        if not chat_session:
            return None

        count_result = await db.execute(
            select(func.count()).select_from(ChatMessage).where(ChatMessage.chat_session_id == chat_session.id)
        )
        message_count = count_result.scalar_one()

        tokens_result = await db.execute(
            select(func.sum(ChatMessage.token_count)).where(ChatMessage.chat_session_id == chat_session.id)
        )
        total_tokens = tokens_result.scalar() or 0

        return {
            "session_id": chat_session.session_id,
            "message_count": message_count,
            "total_tokens": total_tokens,
            "agent_type": chat_session.agent_type.value,
            "created_at": chat_session.created_at.isoformat(),
            "last_message_at": chat_session.last_message_at.isoformat() if chat_session.last_message_at else None
        }

    @staticmethod
    async def get_user_stats(
        db: AsyncSession,
        user_id: str
    ) -> Dict[str, Any]:
        """Get statistics for all user's chat sessions"""
        total_result = await db.execute(
            select(func.count()).select_from(ChatSession).where(ChatSession.user_id == user_id)
        )
        total_sessions = total_result.scalar_one()

        rag_result = await db.execute(
            select(func.count()).select_from(ChatSession).where(
                ChatSession.user_id == user_id,
                ChatSession.agent_type == AgentType.RAG
            )
        )
        rag_sessions = rag_result.scalar_one()

        quant_result = await db.execute(
            select(func.count()).select_from(ChatSession).where(
                ChatSession.user_id == user_id,
                ChatSession.agent_type == AgentType.QUANT
            )
        )
        quant_sessions = quant_result.scalar_one()

        messages_result = await db.execute(
            select(func.count()).select_from(ChatMessage).join(ChatSession).where(
                ChatSession.user_id == user_id
            )
        )
        total_messages = messages_result.scalar_one()

        return {
            "user_id": user_id,
            "total_sessions": total_sessions,
            "rag_sessions": rag_sessions,
            "quant_sessions": quant_sessions,
            "total_messages": total_messages
        }
