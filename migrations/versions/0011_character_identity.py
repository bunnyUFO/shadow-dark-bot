"""add ancestry, alignment, languages to player_characters

Revision ID: 0011_character_identity
Revises: 0010_spell_alignment
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0011_character_identity"
down_revision: Union[str, None] = "0010_spell_alignment"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.add_column(sa.Column("ancestry", sa.String))
        batch_op.add_column(sa.Column("alignment", sa.String))
        batch_op.add_column(sa.Column("languages", sa.String))


def downgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.drop_column("languages")
        batch_op.drop_column("alignment")
        batch_op.drop_column("ancestry")
