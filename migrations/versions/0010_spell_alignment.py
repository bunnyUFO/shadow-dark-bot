"""add alignment column to spells

Revision ID: 0010_spell_alignment
Revises: 0009_spells
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_spell_alignment"
down_revision: Union[str, None] = "0009_spells"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("spells") as batch_op:
        batch_op.add_column(sa.Column("alignment", sa.String))


def downgrade() -> None:
    with op.batch_alter_table("spells") as batch_op:
        batch_op.drop_column("alignment")
