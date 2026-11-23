"""Simplify analyst_reports — drop title, description, recommendation, tags

Revision ID: 011_simplify_analyst_reports
Revises: 010_add_analyst_reports
Create Date: 2026-04-27 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '011_simplify_analyst_reports'
down_revision = '010_add_analyst_reports'
branch_labels = None
depends_on = None


def upgrade():
    # Drop indexes on columns we're about to remove (SQLite batch mode needs this)
    for idx in ("ix_analyst_reports_recommendation",):
        try:
            op.drop_index(idx, table_name="analyst_reports")
        except Exception:
            pass  # Index may not exist yet

    with op.batch_alter_table("analyst_reports", recreate="always") as batch_op:
        for col in ("title", "description", "recommendation", "tags"):
            try:
                batch_op.drop_column(col)
            except Exception:
                pass  # Column may not exist in a fresh DB


def downgrade():
    with op.batch_alter_table("analyst_reports", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("tags", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("recommendation", sa.String(), nullable=True))
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("title", sa.String(), nullable=True))
