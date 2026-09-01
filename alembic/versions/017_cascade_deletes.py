"""Add cascade delete constraints for portfolio relationships

Ensures that deleting a portfolio cascades to all related data:
- analyst_reports
- analysis_tasks
- report_draft_items
- chat_sessions (and their messages)
- sessions

Revision ID: 017_cascade_deletes
Revises: 016_add_analysis_tasks
Create Date: 2026-09-01
"""
from alembic import op
import sqlalchemy as sa


revision = '017_cascade_deletes'
down_revision = '016_add_analysis_tasks'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add FK constraint to report_draft_items.portfolio_id with CASCADE DELETE
    op.create_foreign_key(
        'report_draft_items_portfolio_id_fkey',
        'report_draft_items',
        'portfolios',
        ['portfolio_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Add CASCADE DELETE to existing analyst_reports.portfolio_id FK
    op.drop_constraint('analyst_reports_portfolio_id_fkey', 'analyst_reports', type_='foreignkey')
    op.create_foreign_key(
        'analyst_reports_portfolio_id_fkey',
        'analyst_reports',
        'portfolios',
        ['portfolio_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Add CASCADE DELETE to existing analysis_tasks.portfolio_id FK
    op.drop_constraint('analysis_tasks_portfolio_id_fkey', 'analysis_tasks', type_='foreignkey')
    op.create_foreign_key(
        'analysis_tasks_portfolio_id_fkey',
        'analysis_tasks',
        'portfolios',
        ['portfolio_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Add CASCADE DELETE to existing chat_sessions.portfolio_id FK
    op.drop_constraint('chat_sessions_portfolio_id_fkey', 'chat_sessions', type_='foreignkey')
    op.create_foreign_key(
        'chat_sessions_portfolio_id_fkey',
        'chat_sessions',
        'portfolios',
        ['portfolio_id'],
        ['id'],
        ondelete='CASCADE'
    )

    # Add CASCADE DELETE to existing sessions.portfolio_id FK
    op.drop_constraint('sessions_portfolio_id_fkey', 'sessions', type_='foreignkey')
    op.create_foreign_key(
        'sessions_portfolio_id_fkey',
        'sessions',
        'portfolios',
        ['portfolio_id'],
        ['id'],
        ondelete='CASCADE'
    )


def downgrade() -> None:
    # Restore FKs without CASCADE DELETE
    op.drop_constraint('report_draft_items_portfolio_id_fkey', 'report_draft_items', type_='foreignkey')

    op.drop_constraint('analyst_reports_portfolio_id_fkey', 'analyst_reports', type_='foreignkey')
    op.create_foreign_key(
        'analyst_reports_portfolio_id_fkey',
        'analyst_reports',
        'portfolios',
        ['portfolio_id'],
        ['id']
    )

    op.drop_constraint('analysis_tasks_portfolio_id_fkey', 'analysis_tasks', type_='foreignkey')
    op.create_foreign_key(
        'analysis_tasks_portfolio_id_fkey',
        'analysis_tasks',
        'portfolios',
        ['portfolio_id'],
        ['id']
    )

    op.drop_constraint('chat_sessions_portfolio_id_fkey', 'chat_sessions', type_='foreignkey')
    op.create_foreign_key(
        'chat_sessions_portfolio_id_fkey',
        'chat_sessions',
        'portfolios',
        ['portfolio_id'],
        ['id']
    )

    op.drop_constraint('sessions_portfolio_id_fkey', 'sessions', type_='foreignkey')
    op.create_foreign_key(
        'sessions_portfolio_id_fkey',
        'sessions',
        'portfolios',
        ['portfolio_id'],
        ['id']
    )
