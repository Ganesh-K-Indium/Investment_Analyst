"""Per-user language-model provider credentials (encrypted)

Revision ID: 023_provider_credentials
Revises: 022_user_documents
Create Date: 2026-11-24
"""
from alembic import op
import sqlalchemy as sa

revision = '023_provider_credentials'
down_revision = '022_user_documents'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'provider_credentials',
        sa.Column('user_id', sa.String(), primary_key=True),
        sa.Column('name', sa.String(), primary_key=True),
        sa.Column('value_enc', sa.Text(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table('provider_credentials')
