"""inventory capacity + non-null gear slots

Revision ID: 0003_inventory_capacity
Revises: 0002_value_in_copper
Create Date: 2026-05-21

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_inventory_capacity"
down_revision: Union[str, None] = "0002_value_in_copper"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Backfill any existing NULL gear_slots to 0 BEFORE making the column NOT NULL.
    op.execute("UPDATE items SET gear_slots = 0 WHERE gear_slots IS NULL")

    # items.gear_slots: nullable -> NOT NULL with DEFAULT 0
    with op.batch_alter_table("items") as batch_op:
        batch_op.alter_column(
            "gear_slots",
            existing_type=sa.Float(),
            nullable=False,
            server_default=sa.text("0"),
        )

    # locations.max_gear_slots: new column, NOT NULL with DEFAULT 0
    with op.batch_alter_table("locations") as batch_op:
        batch_op.add_column(
            sa.Column(
                "max_gear_slots",
                sa.Float(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("locations") as batch_op:
        batch_op.drop_column("max_gear_slots")

    with op.batch_alter_table("items") as batch_op:
        batch_op.alter_column(
            "gear_slots",
            existing_type=sa.Float(),
            nullable=True,
            server_default=None,
        )
