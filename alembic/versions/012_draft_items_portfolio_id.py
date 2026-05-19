"""add portfolio_id to report_draft_items

Revision ID: 012_draft_items_portfolio_id
Revises: 011_simplify_analyst_reports
Create Date: 2026-05-19
"""
from alembic import op
import sqlalchemy as sa

revision = '012_draft_items_portfolio_id'
down_revision = '011_simplify_analyst_reports'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'report_draft_items',
        sa.Column('portfolio_id', sa.Integer(), nullable=True)
    )
    op.create_index('ix_report_draft_items_portfolio_id', 'report_draft_items', ['portfolio_id'])


def downgrade():
    op.drop_index('ix_report_draft_items_portfolio_id', table_name='report_draft_items')
    op.drop_column('report_draft_items', 'portfolio_id')
