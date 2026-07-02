"""add proficiencies to player_characters

Revision ID: 0013_character_proficiencies
Revises: 0012_character_background
Create Date: 2026-07-02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0013_character_proficiencies"
down_revision: Union[str, None] = "0012_character_background"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.add_column(sa.Column("proficiencies", sa.Text))


def downgrade() -> None:
    with op.batch_alter_table("player_characters") as batch_op:
        batch_op.drop_column("proficiencies")
