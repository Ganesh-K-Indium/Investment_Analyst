"""User-configurable settings layers

Revision ID: 021_app_settings
Revises: 020_analyst_note_controls
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = '021_app_settings'
down_revision = '020_analyst_note_controls'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'app_settings',
        sa.Column('scope', sa.String(), primary_key=True),
        sa.Column('scope_id', sa.String(), primary_key=True),
        sa.Column('data', sa.JSON(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )


    op.create_table(
        'usage_events',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('user_id', sa.String(), nullable=False, index=True),
        sa.Column('run_label', sa.String(), nullable=True),
        sa.Column('role', sa.String(), nullable=False),
        sa.Column('model', sa.String(), nullable=False),
        sa.Column('prompt_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('completion_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cost_usd', sa.Float(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
    )


def downgrade() -> None:
    op.drop_table('usage_events')
    op.drop_table('app_settings')
