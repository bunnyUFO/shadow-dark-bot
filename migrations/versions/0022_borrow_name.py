"""borrows can be recorded under a free-text name

Revision ID: 0022_borrow_name
Revises: 0021_character_effect_template
Create Date: 2026-10-14

A plain ADD COLUMN on purpose: `borrows` is referenced by foreign keys and a
batch (table rebuild) fails on SQLite with foreign_keys=ON once rows exist.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0022_borrow_name"
down_revision: Union[str, None] = "0021_character_effect_template"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("borrows", sa.Column("borrower_name", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("borrows", "borrower_name")
