"""Canonical Shadow Dark spell reference (Player Quickstart, Tier 1–2).

Seeded into the `spells` table at startup by `seed_spells()`. Higher tiers
(3–5) aren't in the quickstart; players can still track them as freeform
`character_spells`. Each entry: (name, tier, classes, duration, range, text).
"""

from sqlalchemy import select

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import Spell

WIZARD = ("wizard",)
PRIEST = ("priest",)
BOTH = ("priest", "wizard")

# (name, tier, classes, duration, range, description)
SPELLS: tuple[tuple[str, int, tuple[str, ...], str, str, str], ...] = (
    # ---- Tier 1 ----
    (
        "Alarm", 1, WIZARD, "1 day", "Close",
        "You touch one object, such as a door threshold, setting a magical "
        "alarm on it. If any creature you do not designate while casting the "
        "spell touches or crosses past the object, a magical bell sounds in "
        "your head.",
    ),
    (
        "Burning Hands", 1, WIZARD, "Instant", "Close",
        "You spread your fingers with thumbs touching, unleashing a circle of "
        "flame that fills a close area around where you stand. Creatures within "
        "the area take 1d6 damage. Unattended flammable objects ignite.",
    ),
    (
        "Charm Person", 1, WIZARD, "1d8 days", "Near",
        "You magically beguile one humanoid of level 2 or less within near "
        "range, who regards you as a friend for the duration. The spell ends if "
        "you or your allies do anything to hurt it that it notices. The target "
        "knows you enchanted it after the spell ends.",
    ),
    (
        "Cure Wounds", 1, PRIEST, "Instant", "Close",
        "Your touch restores ebbing life. Roll a number of d6s equal to 1 + "
        "half your level (rounded down). One target you touch regains that many "
        "hit points.",
    ),
    (
        "Detect Magic", 1, WIZARD, "Focus", "Near",
        "You can sense the presence of magic within near range for the "
        "duration. If you focus for two rounds, you discern its general "
        "properties. Full barriers block this spell.",
    ),
    (
        "Feather Fall", 1, WIZARD, "Instant", "Self",
        "You may make an attempt to cast this spell when you fall. Your rate of "
        "descent slows so that you land safely on your feet.",
    ),
    (
        "Floating Disk", 1, WIZARD, "10 rounds", "Near",
        "You create a floating, circular disk of force with a concave center. "
        "It can carry up to 20 gear slots, hovers at waist level, and stays "
        "within near of you. It can't cross over drop-offs or pits taller than "
        "a human.",
    ),
    (
        "Hold Portal", 1, WIZARD, "10 rounds", "Near",
        "You magically hold a portal closed for the duration. A creature must "
        "make a successful STR check vs. your spellcasting check to open the "
        "portal. The knock spell ends this spell.",
    ),
    (
        "Holy Weapon", 1, PRIEST, "5 rounds", "Close",
        "One weapon you touch is imbued with a sacred blessing. The weapon "
        "becomes magical and has +1 to attack and damage rolls for the "
        "duration.",
    ),
    (
        "Light", 1, BOTH, "1 hour real time", "Close",
        "One object you touch glows with bright, heatless light, illuminating "
        "out to a near distance for 1 hour of real time.",
    ),
    (
        "Mage Armor", 1, WIZARD, "10 rounds", "Self",
        "An invisible layer of magical force protects your vitals. Your armor "
        "class becomes 14 (18 on a critical spellcasting check) for the "
        "duration.",
    ),
    (
        "Magic Missile", 1, WIZARD, "Instant", "Far",
        "You have advantage on your check to cast this spell. A glowing bolt of "
        "force streaks from your open hand, dealing 1d4 damage to one target.",
    ),
    (
        "Protection From Evil", 1, BOTH, "Focus", "Close",
        "For the duration, chaotic beings have disadvantage on attack rolls and "
        "hostile spellcasting checks against the target, and can't possess, "
        "compel, or beguile it. When cast on an already-possessed target, the "
        "entity makes a CHA check vs. the last spellcasting check; on a failure "
        "it is expelled.",
    ),
    (
        "Shield of Faith", 1, PRIEST, "5 rounds", "Self",
        "A protective force wrought of your holy conviction surrounds you. You "
        "gain a +2 bonus to your armor class for the duration.",
    ),
    (
        "Sleep", 1, WIZARD, "Instant", "Near",
        "You weave a lulling spell that fills a near-sized cube extending from "
        "you. Living creatures LV 2 or less in the area fall into a deep sleep. "
        "Vigorous shaking or being injured wakes them.",
    ),
    (
        "Turn Undead", 1, PRIEST, "Instant", "Near",
        "You rebuke undead creatures, forcing them to flee. You must present a "
        "holy symbol to cast this spell. Undead within near must make a CHA "
        "check vs. your spellcasting check. If a creature fails by 10+ points "
        "and is equal to or less than your level, it is destroyed; otherwise on "
        "a fail it flees from you for 5 rounds.",
    ),
    # ---- Tier 2 ----
    (
        "Acid Arrow", 2, WIZARD, "Focus", "Far",
        "You conjure a corrosive bolt that hits one foe, dealing 1d6 damage a "
        "round. The bolt remains in the target for as long as you focus.",
    ),
    (
        "Alter Self", 2, WIZARD, "5 rounds", "Self",
        "You magically change your physical form, gaining one feature that "
        "modifies your existing anatomy (e.g. gills on your neck or claws on "
        "your fingers). This spell can't grow wings or limbs.",
    ),
    (
        "Augury", 2, PRIEST, "Instant", "Self",
        "You interpret the meaning of supernatural portents and omens. Ask the "
        "GM one question about a specific course of action; the GM says whether "
        "it will lead to “weal” or “woe.”",
    ),
    (
        "Bless", 2, PRIEST, "Instant", "Close",
        "One creature you touch gains a luck token.",
    ),
    (
        "Blind/Deafen", 2, PRIEST, "Focus", "Near",
        "You utter a divine censure, blinding or deafening one creature you can "
        "see in range. The creature has disadvantage on tasks requiring the "
        "lost sense.",
    ),
    (
        "Cleansing Weapon", 2, PRIEST, "5 rounds", "Close",
        "One weapon you touch is wreathed in purifying flames. It deals an "
        "additional 1d4 damage (1d6 vs. undead) for the duration.",
    ),
    (
        "Detect Thoughts", 2, WIZARD, "Focus", "Near",
        "You peer into the mind of one creature you can see within range. Each "
        "round you learn the target's immediate thoughts. On its turn the "
        "target makes a WIS check vs. your last spellcasting check; on a "
        "success it notices you and the spell ends.",
    ),
    (
        "Fixed Object", 2, WIZARD, "5 rounds", "Close",
        "An object you touch that weighs no more than 5 pounds becomes fixed in "
        "its current location. It can support up to 5,000 pounds of weight for "
        "the duration.",
    ),
    (
        "Hold Person", 2, WIZARD, "Focus", "Near",
        "You magically paralyze one humanoid creature of LV 4 or less you can "
        "see within range.",
    ),
    (
        "Invisibility", 2, WIZARD, "10 rounds", "Close",
        "A creature you touch becomes invisible for the duration. The spell "
        "ends if the target attacks or casts a spell.",
    ),
    (
        "Knock", 2, WIZARD, "Instant", "Near",
        "A door, window, gate, chest, or portal you can see within range "
        "instantly opens, defeating all mundane locks and barriers. This spell "
        "creates a loud knock audible to all within earshot.",
    ),
    (
        "Levitate", 2, WIZARD, "Focus", "Self",
        "You can float a near distance vertically per round on your turn. You "
        "can also push against solid objects to move horizontally.",
    ),
    (
        "Mirror Image", 2, WIZARD, "5 rounds", "Self",
        "You create illusory duplicates of yourself equal to half your level "
        "rounded down (minimum 1). Each time a creature attacks you, the attack "
        "misses and destroys one duplicate. When all are gone, the spell ends.",
    ),
    (
        "Misty Step", 2, WIZARD, "Instant", "Self",
        "In a puff of smoke, you teleport a near distance to an area you can "
        "see.",
    ),
    (
        "Silence", 2, WIZARD, "Focus", "Far",
        "You magically mute sound in a near cube within range. Creatures inside "
        "are deafened, and any sounds they create cannot be heard.",
    ),
    (
        "Smite", 2, PRIEST, "Instant", "Near",
        "You call down punishing flames on a creature you can see within range. "
        "It takes 1d6 damage.",
    ),
    (
        "Web", 2, WIZARD, "5 rounds", "Far",
        "You create a near-sized cube of sticky, dense spider web within range. "
        "A creature stuck in the web can't move and must succeed on a STR check "
        "vs. your spellcasting check to free itself.",
    ),
    (
        "Zone of Truth", 2, PRIEST, "Focus", "Near",
        "You compel a creature you can see to speak truth. It can't utter a "
        "deliberate lie while within range.",
    ),
)


def seed_spells() -> None:
    """Idempotently upsert the reference spells by name. Safe to run on every
    startup — new spells are inserted and existing ones refreshed."""
    with session_scope() as session:
        for name, tier, classes, duration, spell_range, description in SPELLS:
            classes_str = ",".join(sorted(classes))
            existing = session.scalar(select(Spell).where(Spell.name == name))
            if existing is None:
                session.add(
                    Spell(
                        name=name,
                        tier=tier,
                        classes=classes_str,
                        duration=duration,
                        range_=spell_range,
                        description=description,
                    )
                )
            else:
                existing.tier = tier
                existing.classes = classes_str
                existing.duration = duration
                existing.range_ = spell_range
                existing.description = description
