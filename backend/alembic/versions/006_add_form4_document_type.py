"""add document_type and period_of_report to form4_transactions

Revision ID: 006_add_form4_document_type
Revises: 005_add_consolidated_summaries
Create Date: 2026-04-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '006_add_form4_document_type'
down_revision = '005_add_consolidated_summaries'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('form4_transactions', sa.Column('document_type', sa.String(), nullable=True))
    op.add_column('form4_transactions', sa.Column('period_of_report', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('form4_transactions', 'period_of_report')
    op.drop_column('form4_transactions', 'document_type')
