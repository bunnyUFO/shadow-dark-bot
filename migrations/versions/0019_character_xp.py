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
    # A plain ADD COLUMN, not batch mode: a batch rebuild drops and recreates
    # player_characters, which fails with foreign_keys=ON once other tables
    # reference its rows. SQLite allows a named CHECK on an added column.
    # Drop any temp table left by an earlier failed batch attempt first.
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_player_characters")
    op.execute(
        "ALTER TABLE player_characters ADD COLUMN xp INTEGER NOT NULL DEFAULT 0 "
        "CONSTRAINT ck_characters_xp_nonneg CHECK (xp >= 0)"
    )


def downgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.drop_constraint("ck_characters_xp_nonneg", type_="check")
        batch_op.drop_column("xp")
