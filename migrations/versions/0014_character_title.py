"""add title to player_characters

Revision ID: 0014_character_title
Revises: 0013_character_proficiencies
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_character_title"
down_revision: Union[str, None] = "0013_character_proficiencies"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.add_column(sa.Column("title", sa.String))


def downgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.drop_column("title")
