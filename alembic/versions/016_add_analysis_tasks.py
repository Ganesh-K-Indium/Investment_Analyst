"""Add analysis_tasks table for background agent run tracking

Backs the async job system: every RAG/quant agent invocation (interactive
chat or batch alpha run) gets a row here, updated PENDING -> RUNNING ->
COMPLETED/FAILED by the Arq worker as it executes.

Revision ID: 016_add_analysis_tasks
Revises: 015_add_token_version
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '016_add_analysis_tasks'
down_revision = '015_add_token_version'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()

    # 'agenttype' already exists (created by migration 002 for chat_sessions) — reuse it.
    agent_type_enum = postgresql.ENUM('rag', 'quant', name='agenttype', create_type=False)

    task_status_enum = postgresql.ENUM('PENDING', 'RUNNING', 'COMPLETED', 'FAILED', name='taskstatus', create_type=False)
    task_status_enum.create(bind, checkfirst=True)

    op.create_table(
        'analysis_tasks',
        sa.Column('id', sa.String(), primary_key=True, index=True),
        sa.Column('user_id', sa.String(), nullable=False, index=True),
        sa.Column('portfolio_id', sa.Integer(), sa.ForeignKey('portfolios.id'), nullable=True, index=True),
        sa.Column('agent_type', agent_type_enum, nullable=False, index=True),
        sa.Column('task_type', sa.String(), nullable=False),
        sa.Column('status', task_status_enum, nullable=False, server_default='PENDING', index=True),
        sa.Column('progress_message', sa.String(), nullable=True),
        sa.Column('result_metadata', sa.JSON(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('analysis_tasks')
    sa.Enum(name='taskstatus').drop(op.get_bind(), checkfirst=True)
    # agenttype enum is not dropped — it may already exist from another table (chat_sessions).
