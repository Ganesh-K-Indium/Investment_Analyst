"""Market-data providers users connect for the quant agent

Revision ID: 025_user_data_sources
Revises: 024_user_mcp_servers
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa

revision = '025_user_data_sources'
down_revision = '024_user_mcp_servers'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_data_sources',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), nullable=False, index=True),
        sa.Column('provider', sa.String(), nullable=False),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('base_url', sa.String(), nullable=True),
        sa.Column('auth_enc', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('user_data_sources')
