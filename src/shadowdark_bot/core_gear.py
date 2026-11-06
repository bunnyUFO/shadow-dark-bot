"""Core rulebook gear: the catalog items that start out as purchasable.

Source: the official Shadowdark Player Quickstart gear tables (basic gear p33,
crawling kit p34, armor p34, weapons p35). No item there is marked unbuyable.
Mithral is a cost multiplier, not an item, and coin and gems ("varies") are not
catalog items, so they are left out. The Quickstart gives no price or
availability rule for +1/+2/+3 items, so those are never marked. The owner can
toggle any item with Purchasable.

Matching happens in migration 0025 and in dev_seed, never at runtime: the
Purchasable flag on the item is the only source of truth. The migration carries
a frozen copy of this set and of `normalize` (deliberately duplicated, so a later
change here cannot alter what 0025 did).
"""

import re

from shadowdark_bot.models import ITEM_TYPE_ARMOR, ITEM_TYPE_COMMON, ITEM_TYPE_WEAPON
from shadowdark_bot.storage import split_bonus_name

CORE_GEAR: frozenset[str] = frozenset(
    {
        # Weapons (Player Quickstart p35)
        "bastard sword", "club", "crossbow", "dagger", "greataxe", "greatsword",
        "javelin", "longbow", "longsword", "mace", "shortbow", "shortsword", "spear",
        "staff", "warhammer",
        # Armor (p34); mithral is a cost multiplier, not an item
        "leather armor", "chainmail", "plate mail", "shield",
        # Basic gear (p33) and the crawling kit (p34). Singular forms and
        # "rope" / "flask of oil" are aliases for how the catalog spells them.
        "arrows", "arrow", "backpack", "caltrops", "crossbow bolts", "crossbow bolt",
        "crowbar", "flask or bottle", "flint and steel", "grappling hook",
        "iron spikes", "iron spike", "lantern", "mirror", "oil flask", "flask of oil",
        "pole", "rations", "ration", "rope 60", "rope", "torch", "crawling kit",
    }
)

CORE_GEAR_TYPES = (ITEM_TYPE_COMMON, ITEM_TYPE_WEAPON, ITEM_TYPE_ARMOR)

_PARENS = re.compile(r"\([^)]*\)")
_PUNCT = re.compile(r"[,.'\"\u2019-]")


def normalize(name: str) -> str:
    """Lowercase, drop parenthesised text, turn punctuation into spaces and
    collapse whitespace: "Rope, 60'" -> "rope 60"."""
    text = _PARENS.sub("", name.lower())
    return " ".join(_PUNCT.sub(" ", text).split())


def is_core_gear(name: str, item_type: str) -> bool:
    """True for a plain (no +N suffix) common, weapon or armor item that is on
    the core gear list."""
    if item_type not in CORE_GEAR_TYPES:
        return False
    if split_bonus_name(name)[1] != 0:
        return False
    return normalize(name) in CORE_GEAR
