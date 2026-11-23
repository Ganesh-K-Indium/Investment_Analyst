"""Add Postgres tsvector full-text search for analyst_reports

Replaces SQLite's FTS5 virtual table/triggers (added in 010, skipped on
Postgres) with a native tsvector column + GIN index + trigger, built against
the final analyst_reports schema (title/description/recommendation/tags were
already dropped by 011; content_html was added by 013). No-op on SQLite,
which keeps using its FTS5 setup from 010.

Revision ID: 014_postgres_fulltext_search
Revises: 013_add_content_html
Create Date: 2026-07-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR

revision = '014_postgres_fulltext_search'
down_revision = '013_add_content_html'
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != 'postgresql':
        return

    op.add_column('analyst_reports', sa.Column('search_vector', TSVECTOR(), nullable=True))

    conn.execute(sa.text("""
        CREATE FUNCTION analyst_reports_search_vector_update() RETURNS trigger AS $$
        BEGIN
            NEW.search_vector :=
                setweight(to_tsvector('english', coalesce(NEW.company_name, '')), 'A') ||
                setweight(to_tsvector('english', coalesce(NEW.content_markdown, '')), 'B');
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
    """))

    conn.execute(sa.text("""
        CREATE TRIGGER reports_search_vector_update
        BEFORE INSERT OR UPDATE ON analyst_reports
        FOR EACH ROW EXECUTE FUNCTION analyst_reports_search_vector_update();
    """))

    op.create_index(
        'ix_analyst_reports_search_vector',
        'analyst_reports',
        ['search_vector'],
        postgresql_using='gin',
    )

    # Backfill existing rows (trigger only fires on future insert/update)
    conn.execute(sa.text("""
        UPDATE analyst_reports SET search_vector =
            setweight(to_tsvector('english', coalesce(company_name, '')), 'A') ||
            setweight(to_tsvector('english', coalesce(content_markdown, '')), 'B');
    """))


def downgrade() -> None:
    conn = op.get_bind()
    if conn.dialect.name != 'postgresql':
        return

    op.drop_index('ix_analyst_reports_search_vector', table_name='analyst_reports')
    conn.execute(sa.text("DROP TRIGGER IF EXISTS reports_search_vector_update ON analyst_reports"))
    conn.execute(sa.text("DROP FUNCTION IF EXISTS analyst_reports_search_vector_update()"))
    op.drop_column('analyst_reports', 'search_vector')
