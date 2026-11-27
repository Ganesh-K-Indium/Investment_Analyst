"""Analysis dashboard revamp: what each run was and produced, per-ticker signals, schedules, run cost

Revision ID: 026_analysis_insights
Revises: 025_user_data_sources
Create Date: 2026-11-27
"""
from alembic import op
import sqlalchemy as sa

revision = '026_analysis_insights'
down_revision = '025_user_data_sources'
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # A new enum value cannot be used in the transaction that adds it, so it is added on its own.
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'CANCELLED'")

    op.create_table(
        'analysis_schedules',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.String(), nullable=False, index=True),
        sa.Column('portfolio_id', sa.Integer(), sa.ForeignKey('portfolios.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('task_type', sa.String(), nullable=False, server_default='alpha'),
        sa.Column('tickers', sa.JSON(), nullable=True),
        sa.Column('trigger', sa.String(), nullable=False, server_default='weekly'),
        sa.Column('hour_utc', sa.Integer(), nullable=False, server_default='6'),
        sa.Column('weekday', sa.Integer(), nullable=True),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('next_run_at', sa.DateTime(), nullable=True, index=True),
        sa.Column('last_run_at', sa.DateTime(), nullable=True),
        sa.Column('last_task_id', sa.String(), nullable=True),
        sa.Column('last_skip_reason', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
    )

    with op.batch_alter_table('analysis_tasks') as t:
        t.add_column(sa.Column('title', sa.String(300), nullable=True))
        t.add_column(sa.Column('tickers', sa.JSON(), nullable=True))
        t.add_column(sa.Column('summary', sa.Text(), nullable=True))
        t.add_column(sa.Column('outcome', sa.JSON(), nullable=True))
        t.add_column(sa.Column('started_at', sa.DateTime(), nullable=True))
        t.add_column(sa.Column('finished_at', sa.DateTime(), nullable=True))
        t.add_column(sa.Column('cost_usd', sa.Float(), nullable=True))
        t.add_column(sa.Column('total_tokens', sa.Integer(), nullable=True))
        t.add_column(sa.Column('error_kind', sa.String(), nullable=True))
        t.add_column(sa.Column('failed_step', sa.String(), nullable=True))
        t.add_column(sa.Column('reviewed_at', sa.DateTime(), nullable=True))
        t.add_column(sa.Column('pinned', sa.Boolean(), nullable=False, server_default=sa.false()))
        t.add_column(sa.Column('tags', sa.JSON(), nullable=True))
        t.add_column(sa.Column('parent_task_id', sa.String(), nullable=True))
        t.add_column(sa.Column('schedule_id', sa.Integer(), nullable=True))
        t.create_index('ix_analysis_tasks_parent_task_id', ['parent_task_id'])
        t.create_index('ix_analysis_tasks_schedule_id', ['schedule_id'])
        t.create_index('idx_analysis_tasks_user_created', ['user_id', 'created_at'])
        t.create_foreign_key('fk_analysis_tasks_schedule', 'analysis_schedules', ['schedule_id'], ['id'], ondelete='SET NULL')

    op.create_table(
        'ticker_signals',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('task_id', sa.String(), sa.ForeignKey('analysis_tasks.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('user_id', sa.String(), nullable=False, index=True),
        sa.Column('portfolio_id', sa.Integer(), sa.ForeignKey('portfolios.id', ondelete='CASCADE'), nullable=True, index=True),
        sa.Column('ticker', sa.String(), nullable=False, index=True),
        sa.Column('stance', sa.String(), nullable=True),
        sa.Column('stance_score', sa.Integer(), nullable=True),
        sa.Column('pillars', sa.JSON(), nullable=True),
        sa.Column('fact_counts', sa.JSON(), nullable=True),
        sa.Column('key_risk', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, index=True),
    )
    op.create_index('idx_ticker_signals_user_ticker', 'ticker_signals', ['user_id', 'ticker', 'created_at'])

    with op.batch_alter_table('usage_events') as t:
        t.add_column(sa.Column('task_id', sa.String(), nullable=True))
        t.create_index('ix_usage_events_task_id', ['task_id'])


def downgrade() -> None:
    with op.batch_alter_table('usage_events') as t:
        t.drop_index('ix_usage_events_task_id')
        t.drop_column('task_id')
    op.drop_index('idx_ticker_signals_user_ticker', table_name='ticker_signals')
    op.drop_table('ticker_signals')
    with op.batch_alter_table('analysis_tasks') as t:
        t.drop_constraint('fk_analysis_tasks_schedule', type_='foreignkey')
        t.drop_index('idx_analysis_tasks_user_created')
        t.drop_index('ix_analysis_tasks_schedule_id')
        t.drop_index('ix_analysis_tasks_parent_task_id')
        for col in ('schedule_id', 'parent_task_id', 'tags', 'pinned', 'reviewed_at', 'failed_step', 'error_kind', 'total_tokens',
                    'cost_usd', 'finished_at', 'started_at', 'outcome', 'summary', 'tickers', 'title'):
            t.drop_column(col)
    op.drop_table('analysis_schedules')
    # Postgres cannot drop one value from an enum; CANCELLED rows are turned into FAILED so the old code can read them.
    if op.get_bind().dialect.name == "postgresql":
        op.execute("UPDATE analysis_tasks SET status = 'FAILED' WHERE status = 'CANCELLED'")
