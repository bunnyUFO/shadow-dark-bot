"""Core rulebook gear: the catalog items that start out as purchasable.

Sources and doubt. The core rulebook PDF was not reachable. Weapons and armor
come from a community quick reference
(https://github.com/Baldrico/Shadowdark/blob/main/Quick%20Reference.md); a second
reference (https://rodsandholes.com/shadowdark_rules_reference.html) agrees,
except that it does not show the javelin. Basic gear comes from Player
Quickstart excerpts (torch, rope, rations, crowbar, grappling hook, iron spikes,
flint and steel, mirror, arrows, lantern); backpack and caltrops from a
third-party copy; pole from the 0-level starting gear. Doubtful: "crawling kit"
(unconfirmed) and whether "flask or bottle" and "pole" are priced lines in the
core table. Mithral armor is left out on purpose: it is a material modifier, not
a line item. The owner can toggle any item with Purchasable.

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
        # Weapons
        "bastard sword", "club", "crossbow", "dagger", "greataxe", "great axe",
        "greatsword", "great sword", "javelin", "longbow", "long bow", "longsword",
        "long sword", "mace", "shortbow", "short bow", "shortsword", "short sword",
        "spear", "staff", "quarterstaff", "warhammer", "war hammer",
        # Armor
        "leather armor", "leather", "chainmail", "chain mail", "chainmail armor",
        "plate mail", "platemail", "plate", "plate armor", "shield",
        # Basic gear
        "arrow", "arrows", "backpack", "caltrops", "crossbow bolt", "crossbow bolts",
        "bolts", "crowbar", "flask", "bottle", "flask or bottle", "flint and steel",
        "grappling hook", "iron spikes", "iron spike", "lantern", "mirror", "oil",
        "oil flask", "flask of oil", "pole", "rations", "ration", "rope", "rope 60",
        "torch", "torches", "crawling kit",
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
