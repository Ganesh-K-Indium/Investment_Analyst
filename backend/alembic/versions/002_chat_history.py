"""Add chat history tables

Revision ID: 002_chat_history
Revises: 001_initial
Create Date: 2026-02-09 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision = '002_chat_history'
down_revision = '001_initial'
branch_labels = None
depends_on = None


def upgrade():
    """Create chat history tables"""
    
    # Create chat_sessions table
    op.create_table(
        'chat_sessions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('session_id', sa.String(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('portfolio_id', sa.Integer(), nullable=True),
        sa.Column('agent_type', sa.Enum('rag', 'quant', name='agenttype'), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True, default=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('last_message_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['portfolio_id'], ['portfolios.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('session_id')
    )
    
    op.create_index(op.f('ix_chat_sessions_agent_type'), 'chat_sessions', ['agent_type'], unique=False)
    op.create_index(op.f('ix_chat_sessions_created_at'), 'chat_sessions', ['created_at'], unique=False)
    op.create_index(op.f('ix_chat_sessions_session_id'), 'chat_sessions', ['session_id'], unique=False)
    op.create_index(op.f('ix_chat_sessions_user_id'), 'chat_sessions', ['user_id'], unique=False)
    
    # Create chat_messages table
    op.create_table(
        'chat_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('chat_session_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.Enum('user', 'assistant', 'system', name='messagerole'), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('message_metadata', sa.JSON(), nullable=True),
        sa.Column('token_count', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['chat_session_id'], ['chat_sessions.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    
    op.create_index(op.f('ix_chat_messages_chat_session_id'), 'chat_messages', ['chat_session_id'], unique=False)
    op.create_index(op.f('ix_chat_messages_created_at'), 'chat_messages', ['created_at'], unique=False)


def downgrade():
    """Drop chat history tables"""
    op.drop_index(op.f('ix_chat_messages_created_at'), table_name='chat_messages')
    op.drop_index(op.f('ix_chat_messages_chat_session_id'), table_name='chat_messages')
    op.drop_table('chat_messages')
    
    op.drop_index(op.f('ix_chat_sessions_user_id'), table_name='chat_sessions')
    op.drop_index(op.f('ix_chat_sessions_session_id'), table_name='chat_sessions')
    op.drop_index(op.f('ix_chat_sessions_created_at'), table_name='chat_sessions')
    op.drop_index(op.f('ix_chat_sessions_agent_type'), table_name='chat_sessions')
    op.drop_table('chat_sessions')
