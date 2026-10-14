"""character XP

Revision ID: 0019_character_xp
Revises: 0018_effects_spell_lost
Create Date: 2026-07-07

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0019_character_xp"
down_revision: Union[str, None] = "0018_effects_spell_lost"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.add_column(
            sa.Column("xp", sa.Integer, nullable=False, server_default="0")
        )
        batch_op.create_check_constraint("ck_characters_xp_nonneg", "xp >= 0")


def downgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.drop_constraint("ck_characters_xp_nonneg", type_="check")
        batch_op.drop_column("xp")
