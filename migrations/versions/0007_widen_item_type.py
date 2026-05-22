"""widen item_type check to 8 values (weapon, armor, loot)

Revision ID: 0007_widen_item_type
Revises: 0006_item_type
Create Date: 2026-05-22

Migration 0006 originally shipped with a 5-value check constraint
(common, magical, crafted, scroll, potion). After 0006 was deployed,
we added weapon/armor/loot to the supported set. Alembic won't replay
0006, so existing databases keep rejecting INSERTs with the new types.
This migration drops and recreates the constraint with all 8 values
so previously-deployed databases catch up. Fresh installs see 0006
create the same 8-value constraint and 0007 then no-op-replace it.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0007_widen_item_type"
down_revision: Union[str, None] = "0006_item_type"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_constraint("ck_items_item_type", type_="check")
        batch_op.create_check_constraint(
            "ck_items_item_type",
            "item_type IN ('common', 'magical', 'crafted', 'scroll', 'potion', "
            "'weapon', 'armor', 'loot')",
        )


def downgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_constraint("ck_items_item_type", type_="check")
        batch_op.create_check_constraint(
            "ck_items_item_type",
            "item_type IN ('common', 'magical', 'crafted', 'scroll', 'potion')",
        )
