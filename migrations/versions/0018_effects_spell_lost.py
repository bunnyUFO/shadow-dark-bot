"""character effects (duration + temp stat changes) and lost spells

Revision ID: 0018_effects_spell_lost
Revises: 0017_creatures
Create Date: 2026-07-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0018_effects_spell_lost"
down_revision: Union[str, None] = "0017_creatures"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "character_effects",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer,
            sa.ForeignKey("player_characters.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("max_rounds", sa.Integer),
        sa.Column("rounds_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("bonuses", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint(
            "max_rounds IS NULL OR (max_rounds >= 1 AND max_rounds <= 100)",
            name="ck_effects_rounds_range",
        ),
    )
    op.create_index(
        "ix_character_effects_character_id", "character_effects", ["character_id"]
    )
    with op.batch_alter_table("character_spells") as batch_op:
        batch_op.add_column(
            sa.Column("lost", sa.Boolean, nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("character_spells") as batch_op:
        batch_op.drop_column("lost")
    op.drop_index("ix_character_effects_character_id", "character_effects")
    op.drop_table("character_effects")
