"""add additional_info to player_characters

Revision ID: 0016_character_additional_info
Revises: 0015_character_locations
Create Date: 2026-07-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0016_character_additional_info"
down_revision: Union[str, None] = "0015_character_locations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "player_characters", sa.Column("additional_info", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.drop_column("additional_info")
