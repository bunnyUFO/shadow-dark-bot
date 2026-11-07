"""magic bonus (+0..+3) on inventory stacks

Adds inventory_entries.bonus and widens the catalog-stack key from
(location_id, item_id) to (location_id, item_id, bonus), so a +0 and a +1
dagger can sit in the same place as separate stacks. Nothing references
inventory_entries, so the table rebuild is safe with foreign keys on.

Revision ID: 0024_stack_bonus
Revises: 0023_creature_tags_kinds
Create Date: 2026-11-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0024_stack_bonus"
down_revision: Union[str, None] = "0023_creature_tags_kinds"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_inventory_entries")
    with op.batch_alter_table("inventory_entries", recreate="always") as b:
        b.add_column(sa.Column("bonus", sa.Integer(), nullable=False, server_default="0"))
        b.drop_constraint("uq_inventory_location_item", type_="unique")
        b.create_unique_constraint(
            "uq_inventory_location_item_bonus", ["location_id", "item_id", "bonus"]
        )
        b.create_check_constraint("ck_inventory_bonus_range", "bonus BETWEEN 0 AND 3")


def downgrade() -> None:
    conn = op.get_bind()
    if conn.execute(sa.text("SELECT 1 FROM inventory_entries WHERE bonus > 0 LIMIT 1")).first():
        raise RuntimeError(
            "Cannot downgrade: inventory_entries has magic-bonus stacks (bonus > 0). "
            "Remove them first; downgrading would merge them into +0 stacks."
        )
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_inventory_entries")
    with op.batch_alter_table("inventory_entries", recreate="always") as b:
        b.drop_constraint("ck_inventory_bonus_range", type_="check")
        b.drop_constraint("uq_inventory_location_item_bonus", type_="unique")
        b.create_unique_constraint("uq_inventory_location_item", ["location_id", "item_id"])
        b.drop_column("bonus")
