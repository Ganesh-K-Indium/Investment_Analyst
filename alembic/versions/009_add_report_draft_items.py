"""Add report_draft_items table for clipboard backend

Revision ID: 009_add_report_draft_items
Revises: 008_add_users
Create Date: 2026-04-23 00:01:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '009_add_report_draft_items'
down_revision = '008_add_users'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    tables = {row[0] for row in conn.execute(sa.text("SELECT name FROM sqlite_master WHERE type='table'"))}

    if 'report_draft_items' not in tables:
        op.create_table(
            'report_draft_items',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column('item_type', sa.String(), nullable=False),
            sa.Column('content', sa.Text(), nullable=True),
            sa.Column('image_url', sa.String(), nullable=True),
            sa.Column('source', sa.String(), nullable=True),
            sa.Column('session_id', sa.String(), nullable=True),
            sa.Column('label', sa.String(), nullable=True),
            sa.Column('sort_order', sa.Integer(), nullable=True, server_default='0'),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_report_draft_items_id', 'report_draft_items', ['id'])
        op.create_index('ix_report_draft_items_user_id', 'report_draft_items', ['user_id'])


def downgrade() -> None:
    op.drop_index('ix_report_draft_items_user_id', table_name='report_draft_items')
    op.drop_index('ix_report_draft_items_id', table_name='report_draft_items')
    op.drop_table('report_draft_items')
