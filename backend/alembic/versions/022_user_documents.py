"""User-uploaded documents

Revision ID: 022_user_documents
Revises: 021_app_settings
Create Date: 2026-11-23
"""
from alembic import op
import sqlalchemy as sa

revision = '022_user_documents'
down_revision = '021_app_settings'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'user_documents',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('user_id', sa.String(), nullable=False, index=True),
        sa.Column('filename', sa.String(), nullable=False),
        sa.Column('ext', sa.String(), nullable=False),
        sa.Column('sha256', sa.String(), nullable=False, index=True),
        sa.Column('size_bytes', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('doc_type', sa.String(), nullable=True),
        sa.Column('label', sa.String(), nullable=True),
        sa.Column('entity', sa.String(), nullable=True),
        sa.Column('period', sa.String(), nullable=True),
        sa.Column('tags', sa.JSON(), nullable=False),
        sa.Column('visibility', sa.String(), nullable=False, server_default='private'),
        sa.Column('status', sa.String(), nullable=False, server_default='queued', index=True),
        sa.Column('stage', sa.String(), nullable=True),
        sa.Column('progress', sa.JSON(), nullable=False),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('pages', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('stats', sa.JSON(), nullable=False),
        sa.Column('reading', sa.JSON(), nullable=False),
        sa.Column('index_policy', sa.String(), nullable=False, server_default='after_review'),
        sa.Column('indexed_pages', sa.JSON(), nullable=False),
        sa.Column('chunk_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('review_pending', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('corrections', sa.JSON(), nullable=False),
        sa.Column('confirmed', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('user_documents')
