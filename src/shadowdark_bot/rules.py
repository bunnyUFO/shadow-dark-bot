"""Shadow Dark rules helpers.

Pure functions with no Discord or database dependencies so they can be unit
tested in isolation. Covers ability modifiers, the spellcasting modifier, the
character carry limit, and the bundle-aware gear-slot cost of a stack.
"""

import math

# Governing spellcasting ability, stored on PlayerCharacter.spell_ability.
SPELL_ABILITY_INT = "int"
SPELL_ABILITY_WIS = "wis"
SPELL_ABILITIES: tuple[str, ...] = (SPELL_ABILITY_INT, SPELL_ABILITY_WIS)

# The six ability scores, in sheet order: (key, display label).
# The score column on PlayerCharacter is f"{key}_score".
ABILITIES: tuple[tuple[str, str], ...] = (
    ("str", "STR"),
    ("dex", "DEX"),
    ("con", "CON"),
    ("int", "INT"),
    ("wis", "WIS"),
    ("cha", "CHA"),
)


def ability_modifier(score: int) -> int:
    """The Shadow Dark ability-score modifier.

    Uses the game's breakpoint table (not ``floor((score - 10) / 2)``):
    1 → −4, 2–3 → −3, 4–5 → −2, 6–8 → −1, 9–12 → +0, 13–15 → +1,
    16–17 → +2, 18 → +3. Scores past either end clamp to the nearest row.
    """
    if score <= 1:
        return -4
    if score <= 3:
        return -3
    if score <= 5:
        return -2
    if score <= 8:
        return -1
    if score <= 12:
        return 0
    if score <= 15:
        return 1
    if score <= 17:
        return 2
    return 3


def format_modifier(mod: int) -> str:
    """Render a modifier with an explicit sign, e.g. ``+1``, ``+0``, ``−2``
    (using a true minus sign for clean alignment in embeds)."""
    if mod < 0:
        return f"−{abs(mod)}"
    return f"+{mod}"


def spellcasting_modifier(governing_score: int, bonus: int) -> int:
    """Spellcasting-check modifier: the governing ability's modifier plus any
    flat bonus granted by talents. Core Shadow Dark rolls ``d20 + this`` against
    ``DC 10 + spell tier``; we surface the modifier, not the roll."""
    return ability_modifier(governing_score) + bonus


def carry_capacity(str_score: int) -> int:
    """Free gear slots for a character: their Strength score, minimum 10."""
    return max(10, str_score)


def stack_slots(quantity: int, gear_slots: float, bundle_size: int) -> float:
    """Slot cost for ``quantity`` of an item: ``ceil(quantity / bundle_size) *
    gear_slots``. A partial bundle still consumes a full bundle's worth of
    slots."""
    return math.ceil(quantity / max(bundle_size, 1)) * gear_slots
