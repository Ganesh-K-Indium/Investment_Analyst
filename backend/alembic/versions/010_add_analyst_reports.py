"""Add analyst_reports table with FTS5 full-text search

Revision ID: 010_add_analyst_reports
Revises: 009_add_report_draft_items
Create Date: 2026-04-23 00:02:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = '010_add_analyst_reports'
down_revision = '009_add_report_draft_items'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    is_sqlite = conn.dialect.name == 'sqlite'
    inspector = inspect(conn)

    if not inspector.has_table('analyst_reports'):
        op.create_table(
            'analyst_reports',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column('title', sa.String(), nullable=False),
            sa.Column('company_name', sa.String(), nullable=False),
            sa.Column('ticker', sa.String(), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('recommendation', sa.String(), nullable=True),
            sa.Column('content_markdown', sa.Text(), nullable=True),
            sa.Column('image_urls', sa.JSON(), nullable=True),
            sa.Column('source_session_ids', sa.JSON(), nullable=True),
            sa.Column('portfolio_id', sa.Integer(), sa.ForeignKey('portfolios.id'), nullable=True),
            sa.Column('status', sa.String(), nullable=True, server_default='draft'),
            sa.Column('tags', sa.JSON(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_analyst_reports_id', 'analyst_reports', ['id'])
        op.create_index('ix_analyst_reports_user_id', 'analyst_reports', ['user_id'])
        op.create_index('ix_analyst_reports_company_name', 'analyst_reports', ['company_name'])
        op.create_index('ix_analyst_reports_ticker', 'analyst_reports', ['ticker'])
        op.create_index('ix_analyst_reports_recommendation', 'analyst_reports', ['recommendation'])
        op.create_index('ix_analyst_reports_status', 'analyst_reports', ['status'])
        op.create_index('ix_analyst_reports_created_at', 'analyst_reports', ['created_at'])

    # FTS5 virtual table for full-text search — SQLite only. Postgres gets its
    # own tsvector/GIN-based full-text search, added in a later migration
    # (014_postgres_fulltext_search) against the final post-cleanup schema,
    # since migrations 011/013 still go on to drop/add columns after this one.
    if is_sqlite:
        conn.execute(sa.text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS analyst_reports_fts USING fts5(
                title,
                company_name,
                description,
                content_markdown,
                content='analyst_reports',
                content_rowid='id'
            )
        """))

        # Sync triggers — keep FTS index in step with the main table
        conn.execute(sa.text("""
            CREATE TRIGGER IF NOT EXISTS reports_fts_insert
            AFTER INSERT ON analyst_reports BEGIN
                INSERT INTO analyst_reports_fts(rowid, title, company_name, description, content_markdown)
                VALUES (new.id, new.title, new.company_name, new.description, new.content_markdown);
            END
        """))

        conn.execute(sa.text("""
            CREATE TRIGGER IF NOT EXISTS reports_fts_update
            AFTER UPDATE ON analyst_reports BEGIN
                UPDATE analyst_reports_fts
                SET title=new.title, company_name=new.company_name,
                    description=new.description, content_markdown=new.content_markdown
                WHERE rowid=new.id;
            END
        """))

        conn.execute(sa.text("""
            CREATE TRIGGER IF NOT EXISTS reports_fts_delete
            AFTER DELETE ON analyst_reports BEGIN
                DELETE FROM analyst_reports_fts WHERE rowid=old.id;
            END
        """))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name == 'sqlite':
        conn.execute(sa.text("DROP TRIGGER IF EXISTS reports_fts_delete"))
        conn.execute(sa.text("DROP TRIGGER IF EXISTS reports_fts_update"))
        conn.execute(sa.text("DROP TRIGGER IF EXISTS reports_fts_insert"))
        conn.execute(sa.text("DROP TABLE IF EXISTS analyst_reports_fts"))
    op.drop_index('ix_analyst_reports_created_at', table_name='analyst_reports')
    op.drop_index('ix_analyst_reports_status', table_name='analyst_reports')
    op.drop_index('ix_analyst_reports_recommendation', table_name='analyst_reports')
    op.drop_index('ix_analyst_reports_ticker', table_name='analyst_reports')
    op.drop_index('ix_analyst_reports_company_name', table_name='analyst_reports')
    op.drop_index('ix_analyst_reports_user_id', table_name='analyst_reports')
    op.drop_index('ix_analyst_reports_id', table_name='analyst_reports')
    op.drop_table('analyst_reports')
