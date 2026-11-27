"""Per-pillar detail of an ALPHA signal: each pillar's score, verdict, fact-check result; partial runs

Revision ID: 029_signal_detail
Revises: 028_usage_priced
Create Date: 2026-11-27
"""
from alembic import op
import sqlalchemy as sa

revision = '029_signal_detail'
down_revision = '028_usage_priced'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('ticker_signals') as t:
        t.add_column(sa.Column('detail', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('ticker_signals') as t:
        t.drop_column('detail')
