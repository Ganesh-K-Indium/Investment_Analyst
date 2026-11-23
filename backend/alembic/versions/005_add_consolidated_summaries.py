"""add consolidated_summaries table

Revision ID: 005_add_consolidated_summaries
Revises: 004_add_form4_transactions
Create Date: 2026-03-05 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '005_add_consolidated_summaries'
down_revision = '004_add_form4_transactions'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'consolidated_summaries',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('user_id', sa.String(), nullable=False),
        sa.Column('session_ids', sa.JSON(), nullable=False),
        sa.Column('detected_type', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=True),
        sa.Column('summary', sa.Text(), nullable=False),
        sa.Column('sessions_included', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_consolidated_summaries_id', 'consolidated_summaries', ['id'])
    op.create_index('ix_consolidated_summaries_user_id', 'consolidated_summaries', ['user_id'])
    op.create_index('ix_consolidated_summaries_created_at', 'consolidated_summaries', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_consolidated_summaries_created_at', table_name='consolidated_summaries')
    op.drop_index('ix_consolidated_summaries_user_id', table_name='consolidated_summaries')
    op.drop_index('ix_consolidated_summaries_id', table_name='consolidated_summaries')
    op.drop_table('consolidated_summaries')
