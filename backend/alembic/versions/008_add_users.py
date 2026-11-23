"""Add users table for analyst/fund manager auth

Revision ID: 008_add_users
Revises: 007_add_form4_has_common_stock
Create Date: 2026-04-23 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '008_add_users'
down_revision = '007_add_form4_has_common_stock'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    if not inspector.has_table('users'):
        op.create_table(
            'users',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(), nullable=False),
            sa.Column('username', sa.String(), nullable=False),
            sa.Column('full_name', sa.String(), nullable=True),
            sa.Column('role', sa.String(), nullable=False, server_default='analyst'),
            sa.Column('hashed_password', sa.String(), nullable=False),
            sa.Column('is_active', sa.Boolean(), nullable=True, server_default='1'),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_users_id', 'users', ['id'])
        op.create_index('ix_users_email', 'users', ['email'], unique=True)
        op.create_index('ix_users_username', 'users', ['username'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_username', table_name='users')
    op.drop_index('ix_users_email', table_name='users')
    op.drop_index('ix_users_id', table_name='users')
    op.drop_table('users')
