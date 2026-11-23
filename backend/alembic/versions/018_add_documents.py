"""Add documents registry table

Synced cache of the documents ingested into Qdrant (see docs/DOCUMENT_INSPECTOR_PLAN.md §5.1).

Revision ID: 018_add_documents
Revises: 017_cascade_deletes
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa


revision = '018_add_documents'
down_revision = '017_cascade_deletes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'documents',
        sa.Column('doc_id', sa.String(), primary_key=True),
        sa.Column('ticker', sa.String(), nullable=True, index=True),
        sa.Column('company', sa.String(), nullable=True, index=True),
        sa.Column('form_type', sa.String(), nullable=True, index=True),
        sa.Column('fiscal_year', sa.Integer(), nullable=True, index=True),
        sa.Column('fiscal_quarter', sa.Integer(), nullable=True),
        sa.Column('period_end_date', sa.String(), nullable=True),
        sa.Column('filing_date', sa.Date(), nullable=True),
        sa.Column('accession', sa.String(), nullable=True),
        sa.Column('source_file', sa.String(), nullable=False),
        sa.Column('collection', sa.String(), nullable=False),
        sa.Column('page_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('text_pages', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('image_chunks', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('pdf_status', sa.String(), nullable=False, server_default='missing'),
        sa.Column('page_fingerprints', sa.JSON(), nullable=True),
        sa.Column('ingested_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('removed', sa.Boolean(), nullable=False, server_default=sa.false(), index=True),
    )


def downgrade() -> None:
    op.drop_table('documents')
