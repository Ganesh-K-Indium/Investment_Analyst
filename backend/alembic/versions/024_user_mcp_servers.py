"""MCP servers users connect for the quant agent

Revision ID: 024_user_mcp_servers
Revises: 023_provider_credentials
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa

revision = '024_user_mcp_servers'
down_revision = '023_provider_credentials'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_mcp_servers',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), nullable=False, index=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('url', sa.String(), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('auth_enc', sa.Text(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('allow_actions', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('tools', sa.JSON(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('user_mcp_servers')
