"""
Database models for portfolio and session management
"""
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text, Boolean, Enum as SQLEnum
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import relationship
from datetime import datetime
import enum

Base = declarative_base()


class AgentType(str, enum.Enum):
    """Enum for different agent types"""
    RAG = "rag"
    QUANT = "quant"


class MessageRole(str, enum.Enum):
    """Enum for message roles"""
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class Portfolio(Base):
    """Portfolio model to store company collections for RAG filtering"""
    __tablename__ = "portfolios"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    name = Column(String, nullable=False)
    company_names = Column(JSON, nullable=False)  # List of company names
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    sessions = relationship("Session", back_populates="portfolio", cascade="all, delete-orphan")
    chat_sessions = relationship("ChatSession", back_populates="portfolio", cascade="all, delete-orphan")


class Session(Base):
    """Session model to track active portfolio sessions (legacy - for backwards compatibility)"""
    __tablename__ = "sessions"
    
    id = Column(String, primary_key=True, index=True)  # thread_id
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=False)
    user_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_accessed = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationship
    portfolio = relationship("Portfolio", back_populates="sessions")


class ChatSession(Base):
    """Chat session model - tracks conversations across different agents"""
    __tablename__ = "chat_sessions"
    
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String, unique=True, nullable=False, index=True)  # thread_id/session_id
    user_id = Column(String, nullable=False, index=True)
    portfolio_id = Column(Integer, ForeignKey("portfolios.id"), nullable=True)  # Optional
    agent_type = Column(SQLEnum(AgentType), nullable=False, index=True)  # rag or quant
    
    # Session metadata
    title = Column(String, nullable=True)  # Auto-generated or user-provided title
    is_active = Column(Boolean, default=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    last_message_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # Relationships
    portfolio = relationship("Portfolio", back_populates="chat_sessions")
    messages = relationship("ChatMessage", back_populates="chat_session", cascade="all, delete-orphan", order_by="ChatMessage.created_at")


class ChatMessage(Base):
    """Individual chat messages within a session"""
    __tablename__ = "chat_messages"
    
    id = Column(Integer, primary_key=True, index=True)
    chat_session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    
    # Message content
    role = Column(SQLEnum(MessageRole), nullable=False)
    content = Column(Text, nullable=False)
    
    # Additional metadata - renamed from 'metadata' to 'message_metadata' to avoid SQLAlchemy conflict
    message_metadata = Column(JSON, nullable=True)
    token_count = Column(Integer, nullable=True)
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Relationship
    chat_session = relationship("ChatSession", back_populates="messages")


class Integration(Base):
    """Integration model to store data source connector configurations"""
    __tablename__ = "integrations"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False, index=True)
    vendor = Column(String, nullable=False)  # sharepoint, google_drive, azure_blob, aws_s3, sftp
    name = Column(String, nullable=False)  # User-friendly name for this integration
    url = Column(String, nullable=True)  # Connection URL (for SharePoint, Azure, etc.)
    
    # Authentication credentials (stored as JSON for flexibility)
    credentials = Column(JSON, nullable=False)  # {client_id, client_secret, user_id, folder_path, etc}
    
    # Connection status
    status = Column(String, default="active")  # active, disconnected, error
    last_sync = Column(DateTime, nullable=True)
    
    # Metadata
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
