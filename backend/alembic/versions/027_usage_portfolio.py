"""Spend per portfolio, and removal of runs left behind by deleted portfolios and chats

Revision ID: 027_usage_portfolio
Revises: 026_analysis_insights
Create Date: 2026-11-27
"""
from alembic import op
import sqlalchemy as sa

revision = '027_usage_portfolio'
down_revision = '026_analysis_insights'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('usage_events') as t:
        t.add_column(sa.Column('portfolio_id', sa.Integer(), nullable=True))
        t.create_index('ix_usage_events_portfolio_id', ['portfolio_id'])

    if op.get_bind().dialect.name != "postgresql":
        return
    # Usage of background runs already knows its portfolio through the run.
    op.execute("""
        UPDATE usage_events u SET portfolio_id = t.portfolio_id
        FROM analysis_tasks t WHERE u.task_id = t.id AND u.portfolio_id IS NULL AND t.portfolio_id IS NOT NULL
    """)
    # Usage recorded by chats: the run label carries the chat's thread id ("ask:<thread>", "compare:<thread>", "alpha:<thread>:<T>").
    op.execute("""
        UPDATE usage_events u SET portfolio_id = c.portfolio_id
        FROM chat_sessions c
        WHERE u.portfolio_id IS NULL AND c.portfolio_id IS NOT NULL
          AND split_part(u.run_label, ':', 2) <> '' AND c.session_id = split_part(u.run_label, ':', 2)
    """)
    # Runs with no portfolio whose chat no longer exists: what remains of deleted portfolios and chats. They have no page to
    # open and only cluttered the history.
    op.execute("""
        DELETE FROM analysis_tasks t
        WHERE t.portfolio_id IS NULL
          AND NOT EXISTS (
              SELECT 1 FROM chat_sessions c
              WHERE c.session_id = COALESCE(t.result_metadata::json ->> 'session_ref',
                                            t.result_metadata::json -> 'response' ->> 'thread_id',
                                            t.result_metadata::json -> 'response' ->> 'session_id'))
    """)


def downgrade() -> None:
    with op.batch_alter_table('usage_events') as t:
        t.drop_index('ix_usage_events_portfolio_id')
        t.drop_column('portfolio_id')
