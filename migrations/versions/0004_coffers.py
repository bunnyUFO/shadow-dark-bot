"""add coffers singleton table

Revision ID: 0004_coffers
Revises: 0003_inventory_capacity
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_coffers"
down_revision: Union[str, None] = "0003_inventory_capacity"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "coffers",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "balance_cp",
            sa.Integer,
            nullable=False,
            server_default=sa.text("0"),
        ),
    )


def downgrade() -> None:
    op.drop_table("coffers")
