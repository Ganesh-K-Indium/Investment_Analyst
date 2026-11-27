"""Usage of a model whose price is unknown is marked, instead of being stored as a silent $0

Revision ID: 028_usage_priced
Revises: 027_usage_portfolio
Create Date: 2026-11-27
"""
from alembic import op
import sqlalchemy as sa

revision = '028_usage_priced'
down_revision = '027_usage_portfolio'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('usage_events') as t:
        t.add_column(sa.Column('priced', sa.Boolean(), nullable=False, server_default=sa.true()))
    # Stored costs are recomputed with the current price table by `python scripts/dev.py backfill-insights`
    # (app/services/usage_ledger.reprice), which also sets this flag for models with no known price.


def downgrade() -> None:
    with op.batch_alter_table('usage_events') as t:
        t.drop_column('priced')
