"""add content_html to analyst_reports and html to report_draft_items

Revision ID: 013_add_content_html
Revises: 012_draft_items_portfolio_id
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa

revision = '013_add_content_html'
down_revision = '012_draft_items_portfolio_id'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('analyst_reports', sa.Column('content_html', sa.Text(), nullable=True))
    op.add_column('report_draft_items', sa.Column('html', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('report_draft_items', 'html')
    op.drop_column('analyst_reports', 'content_html')
