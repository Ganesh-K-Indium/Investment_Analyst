"""add session_metadata to chat_sessions

Revision ID: 003_add_session_metadata
Revises: 20b80069323a
Create Date: 2026-02-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '003_add_session_metadata'
down_revision = '20b80069323a'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'chat_sessions',
        sa.Column('session_metadata', sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('chat_sessions', 'session_metadata')
