"""add has_common_stock and ingested_at to form4_transactions

Revision ID: 007_add_form4_has_common_stock
Revises: 006_add_form4_document_type
Create Date: 2026-04-20 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision = '007_add_form4_has_common_stock'
down_revision = '006_add_form4_document_type'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    existing = {row[1] for row in conn.execute(sa.text("PRAGMA table_info(form4_transactions)"))}
    if 'has_common_stock' not in existing:
        op.add_column('form4_transactions', sa.Column('has_common_stock', sa.Boolean(), nullable=True))
    if 'ingested_at' not in existing:
        op.add_column('form4_transactions', sa.Column('ingested_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column('form4_transactions', 'ingested_at')
    op.drop_column('form4_transactions', 'has_common_stock')
