"""player characters: stats + inventory

Revision ID: 0008_player_characters
Revises: 0007_widen_item_type
Create Date: 2026-07-01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_player_characters"
down_revision: Union[str, None] = "0007_widen_item_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "player_characters",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.String, nullable=False, unique=True),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("char_class", sa.String),
        sa.Column("level", sa.Integer, nullable=False, server_default="1"),
        sa.Column("max_hp", sa.Integer),
        sa.Column("armor_class", sa.Integer),
        sa.Column("str_score", sa.Integer, nullable=False, server_default="10"),
        sa.Column("dex_score", sa.Integer, nullable=False, server_default="10"),
        sa.Column("con_score", sa.Integer, nullable=False, server_default="10"),
        sa.Column("int_score", sa.Integer, nullable=False, server_default="10"),
        sa.Column("wis_score", sa.Integer, nullable=False, server_default="10"),
        sa.Column("cha_score", sa.Integer, nullable=False, server_default="10"),
        sa.Column("gold_cp", sa.Integer, nullable=False, server_default="0"),
        sa.Column("talents", sa.Text),
        sa.Column("spell_ability", sa.String),
        sa.Column("spell_check_bonus", sa.Integer, nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("level >= 1", name="ck_characters_level_positive"),
        sa.CheckConstraint(
            "spell_ability IN ('int', 'wis')", name="ck_characters_spell_ability"
        ),
    )

    op.create_table(
        "character_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer,
            sa.ForeignKey("player_characters.id"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Integer, sa.ForeignKey("items.id")),
        sa.Column("name", sa.String),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("slots_each", sa.Float),
        sa.Column("bundle_size", sa.Integer),
        sa.Column("notes", sa.Text),
        sa.Column(
            "added_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint(
            "quantity > 0", name="ck_character_items_quantity_positive"
        ),
        sa.UniqueConstraint("character_id", "item_id", name="uq_character_item"),
    )


def downgrade() -> None:
    op.drop_table("character_items")
    op.drop_table("player_characters")
