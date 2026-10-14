"""creatures bestiary + encounters

Revision ID: 0017_creatures
Revises: 0016_character_additional_info
Create Date: 2026-07-05

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0017_creatures"
down_revision: Union[str, None] = "0016_character_additional_info"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "creatures",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("level", sa.Integer),
        sa.Column("ac", sa.Integer),
        sa.Column("hp", sa.Integer),
        sa.Column("attacks", sa.Text),
        sa.Column("movement", sa.String),
        sa.Column("str_mod", sa.Integer),
        sa.Column("dex_mod", sa.Integer),
        sa.Column("con_mod", sa.Integer),
        sa.Column("int_mod", sa.Integer),
        sa.Column("wis_mod", sa.Integer),
        sa.Column("cha_mod", sa.Integer),
        sa.Column("alignment", sa.String),
        sa.Column("special_abilities", sa.Text),
        sa.Column("description", sa.Text),
        sa.Column("notes", sa.Text),
        sa.Column("created_by", sa.String),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at", sa.DateTime, nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_table(
        "creature_encounters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "creature_id", sa.Integer,
            sa.ForeignKey("creatures.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("session", sa.String),
        sa.Column("place", sa.String),
        sa.Column("outcome", sa.Text),
        sa.Column("created_by", sa.String),
        sa.Column(
            "created_at", sa.DateTime, nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )
    op.create_index(
        "ix_creature_encounters_creature_id", "creature_encounters", ["creature_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_creature_encounters_creature_id", "creature_encounters")
    op.drop_table("creature_encounters")
    op.drop_table("creatures")
