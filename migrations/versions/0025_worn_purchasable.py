"""worn armor and purchasable items

Adds inventory_entries.worn (only worn armor counts toward AC) and
items.purchasable (whether the Items page offers Buy). Both are plain ADD COLUMN
calls: items is referenced by foreign keys, so a batch rebuild would fail with
foreign_keys=ON (see 0023).

Data passes, in Python over the connection:
- purchasable: common, weapon and armor items with no " +N" suffix whose
  normalized name is in the core gear list below.
- worn: per held location, the armor stack M4's rule counted for AC (the
  highest-bonus body armor and the highest-bonus shield among bonus > 0, ties by
  lowest id) is marked worn, so AC does not change silently. +0 armor stays
  unworn.

The gear list and normalize() are a frozen copy of shadowdark_bot/core_gear.py,
deliberately duplicated: a migration must not import app code that changes later.

Revision ID: 0025_worn_purchasable
Revises: 0024_stack_bonus
Create Date: 2026-11-06

"""
import re
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0025_worn_purchasable"
down_revision: Union[str, None] = "0024_stack_bonus"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Frozen copy of core_gear.CORE_GEAR (deliberately duplicated, see docstring).
_CORE_GEAR = frozenset(
    {
        "bastard sword", "club", "crossbow", "dagger", "greataxe", "greatsword",
        "javelin", "longbow", "longsword", "mace", "shortbow", "shortsword", "spear",
        "staff", "warhammer",
        "leather armor", "chainmail", "plate mail", "shield",
        "arrows", "arrow", "backpack", "caltrops", "crossbow bolts", "crossbow bolt",
        "crowbar", "flask or bottle", "flint and steel", "grappling hook",
        "iron spikes", "iron spike", "lantern", "mirror", "oil flask", "flask of oil",
        "pole", "rations", "ration", "rope 60", "rope", "torch", "crawling kit",
    }
)
_PARENS = re.compile(r"\([^)]*\)")
_PUNCT = re.compile(r"[,.'\"\u2019-]")
_BONUS_SUFFIX = re.compile(r"\s\+[1-3]$")


def _normalize(name: str) -> str:
    text = _PARENS.sub("", name.lower())
    return " ".join(_PUNCT.sub(" ", text).split())


def upgrade() -> None:
    op.add_column(
        "items",
        sa.Column(
            "purchasable", sa.Boolean(create_constraint=False),
            nullable=False, server_default="0",
        ),
    )
    op.add_column(
        "inventory_entries",
        sa.Column(
            "worn", sa.Boolean(create_constraint=False),
            nullable=False, server_default="0",
        ),
    )
    conn = op.get_bind()

    items = conn.execute(sa.text("SELECT id, name FROM items WHERE item_type IN "
                                 "('common', 'weapon', 'armor')")).all()
    for item_id, name in items:
        if _BONUS_SUFFIX.search(name.strip()):
            continue
        if _normalize(name) in _CORE_GEAR:
            conn.execute(
                sa.text("UPDATE items SET purchasable = 1 WHERE id = :id"), {"id": item_id}
            )

    rows = conn.execute(
        sa.text(
            "SELECT e.id, e.location_id, e.bonus, i.name FROM inventory_entries e "
            "JOIN items i ON i.id = e.item_id "
            "JOIN locations l ON l.id = e.location_id "
            "WHERE l.role = 'held' AND i.item_type = 'armor' AND e.bonus > 0 "
            "ORDER BY e.id"
        )
    ).all()
    best: dict[tuple[int, bool], tuple[int, int]] = {}  # key -> (bonus, entry id)
    for entry_id, location_id, bonus, name in rows:
        key = (location_id, "shield" in name.lower())
        if key not in best or bonus > best[key][0]:  # rows are by id: ties keep lowest
            best[key] = (bonus, entry_id)
    for _bonus, entry_id in best.values():
        conn.execute(
            sa.text("UPDATE inventory_entries SET worn = 1 WHERE id = :id"), {"id": entry_id}
        )


def downgrade() -> None:
    op.drop_column("inventory_entries", "worn")
    op.drop_column("items", "purchasable")
