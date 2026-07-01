"""spell reference + character known spells

Revision ID: 0009_spells
Revises: 0008_player_characters
Create Date: 2026-07-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_spells"
down_revision: Union[str, None] = "0008_player_characters"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "spells",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("tier", sa.Integer, nullable=False),
        sa.Column("classes", sa.String, nullable=False),
        sa.Column("duration", sa.String),
        sa.Column("range", sa.String),
        sa.Column("description", sa.Text),
        sa.CheckConstraint("tier >= 1", name="ck_spells_tier_positive"),
    )

    op.create_table(
        "character_spells",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer,
            sa.ForeignKey("player_characters.id"),
            nullable=False,
        ),
        sa.Column("spell_id", sa.Integer, sa.ForeignKey("spells.id")),
        sa.Column("name", sa.String),
        sa.Column("tier", sa.Integer),
        sa.Column(
            "added_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.UniqueConstraint("character_id", "spell_id", name="uq_character_spell"),
    )


def downgrade() -> None:
    op.drop_table("character_spells")
    op.drop_table("spells")
