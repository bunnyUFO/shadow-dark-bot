"""store item value as integer copper

Revision ID: 0002_value_in_copper
Revises: 0001_initial
Create Date: 2026-05-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002_value_in_copper"
down_revision: Union[str, None] = "0001_initial"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.add_column(sa.Column("value_cp", sa.Integer, nullable=True))

    # Backfill: any existing value_gp (REAL) becomes value_cp (INTEGER, * 100)
    op.execute(
        "UPDATE items SET value_cp = CAST(ROUND(value_gp * 100) AS INTEGER) "
        "WHERE value_gp IS NOT NULL"
    )

    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_column("value_gp")


def downgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.add_column(sa.Column("value_gp", sa.Float, nullable=True))

    op.execute(
        "UPDATE items SET value_gp = value_cp / 100.0 "
        "WHERE value_cp IS NOT NULL"
    )

    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_column("value_cp")
