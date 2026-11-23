"""Analyst note controls: owner pause + per-user opt-in to shared notes

analyst_notes.active   owner's "hide for now" (False = stored, but out of everyone's analysis)
analyst_note_inclusions  a user's opt-in to use another analyst's shared note in their own analysis

Revision ID: 020_analyst_note_controls
Revises: 019_add_analyst_notes
Create Date: 2026-11-05
"""
from alembic import op
import sqlalchemy as sa


revision = '020_analyst_note_controls'
down_revision = '019_add_analyst_notes'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('analyst_notes', sa.Column('active', sa.Boolean(), nullable=False, server_default=sa.true()))
    op.create_table(
        'analyst_note_inclusions',
        sa.Column('user_id', sa.String(), primary_key=True),
        sa.Column('note_id', sa.String(), sa.ForeignKey('analyst_notes.note_id', ondelete='CASCADE'), primary_key=True, index=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table('analyst_note_inclusions')
    op.drop_column('analyst_notes', 'active')
