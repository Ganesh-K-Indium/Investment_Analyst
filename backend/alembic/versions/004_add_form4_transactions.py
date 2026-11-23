"""add form4_transactions table

Revision ID: 004_add_form4_transactions
Revises: 003_add_session_metadata
Create Date: 2026-02-26 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '004_add_form4_transactions'
down_revision = '003_add_session_metadata'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'form4_transactions',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('accession_number', sa.String(), nullable=False),
        sa.Column('issuer_symbol', sa.String(), nullable=False),
        sa.Column('issuer_name', sa.String(), nullable=True),
        sa.Column('rpt_owner_name', sa.String(), nullable=False),
        sa.Column('rpt_owner_title', sa.String(), nullable=True),
        sa.Column('is_director', sa.Boolean(), nullable=True),
        sa.Column('is_officer', sa.Boolean(), nullable=True),
        sa.Column('is_ten_percent_owner', sa.Boolean(), nullable=True),
        sa.Column('transaction_date', sa.Date(), nullable=True),
        sa.Column('transaction_code', sa.String(), nullable=True),
        sa.Column('transaction_shares', sa.Float(), nullable=True),
        sa.Column('transaction_price_per_share', sa.Float(), nullable=True),
        sa.Column('transaction_acquired_disposed_code', sa.String(), nullable=True),
        sa.Column('security_title', sa.String(), nullable=True),
        sa.Column('transaction_value', sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('idx_f4_accession_number', 'form4_transactions', ['accession_number'])
    op.create_index('idx_f4_issuer_symbol', 'form4_transactions', ['issuer_symbol'])
    op.create_index('idx_f4_rpt_owner_name', 'form4_transactions', ['rpt_owner_name'])
    op.create_index('idx_f4_transaction_date', 'form4_transactions', ['transaction_date'])
    op.create_index('idx_symbol_date', 'form4_transactions', ['issuer_symbol', 'transaction_date'])


def downgrade() -> None:
    op.drop_index('idx_symbol_date', table_name='form4_transactions')
    op.drop_index('idx_f4_transaction_date', table_name='form4_transactions')
    op.drop_index('idx_f4_rpt_owner_name', table_name='form4_transactions')
    op.drop_index('idx_f4_issuer_symbol', table_name='form4_transactions')
    op.drop_index('idx_f4_accession_number', table_name='form4_transactions')
    op.drop_table('form4_transactions')
