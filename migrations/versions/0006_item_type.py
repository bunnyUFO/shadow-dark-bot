"""replace is_magical with item_type enum

Revision ID: 0006_item_type
Revises: 0005_bundle_size
Create Date: 2026-05-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_item_type"
down_revision: Union[str, None] = "0005_bundle_size"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "item_type",
                sa.String,
                nullable=False,
                server_default="common",
            )
        )
        batch_op.create_check_constraint(
            "ck_items_item_type",
            "item_type IN ('common', 'magical', 'crafted', 'scroll', 'potion')",
        )

    op.execute("UPDATE items SET item_type = 'magical' WHERE is_magical = 1")

    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_column("is_magical")


def downgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_magical",
                sa.Boolean,
                nullable=False,
                server_default=sa.false(),
            )
        )

    op.execute("UPDATE items SET is_magical = 1 WHERE item_type = 'magical'")

    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_constraint("ck_items_item_type", type_="check")
        batch_op.drop_column("item_type")
