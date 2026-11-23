"""Add token_version to users for real logout/session revocation

JWTs are stateless — without this, a "logged out" or leaked token stays
valid until its natural expiry (30min access / 7day refresh). Embedding the
user's token_version in every issued token and checking it against the
current DB value on every auth check lets logout/password-change actually
invalidate outstanding tokens.

Revision ID: 015_add_token_version
Revises: 014_postgres_fulltext_search
Create Date: 2026-07-30
"""
from alembic import op
import sqlalchemy as sa

revision = '015_add_token_version'
down_revision = '014_postgres_fulltext_search'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('token_version', sa.Integer(), nullable=False, server_default='0'))


def downgrade() -> None:
    op.drop_column('users', 'token_version')
