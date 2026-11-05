"""Add analyst_notes table

Per-analyst research notes (see app/services/analyst_notes.py). Chunks live in the separate `analyst_notes` Qdrant
collection; this table owns ownership/visibility and the original text.

Revision ID: 019_add_analyst_notes
Revises: 018_add_documents
Create Date: 2026-11-04
"""
from alembic import op
import sqlalchemy as sa


revision = '019_add_analyst_notes'
down_revision = '018_add_documents'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'analyst_notes',
        sa.Column('note_id', sa.String(), primary_key=True),
        sa.Column('owner_id', sa.String(), nullable=False, index=True),
        sa.Column('author', sa.String(), nullable=False),
        sa.Column('ticker', sa.String(), nullable=False, index=True),
        sa.Column('title', sa.String(), nullable=False),
        sa.Column('note_date', sa.Date(), nullable=False),
        sa.Column('visibility', sa.String(), nullable=False, server_default='private'),
        sa.Column('input_method', sa.String(), nullable=False, server_default='typed'),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('chunk_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('analyst_notes')
