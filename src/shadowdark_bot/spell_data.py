"""Canonical Shadow Dark spell reference, seeded into the `spells` table at
startup by `seed_spells()`. Each entry: (name, tier, classes, duration, range,
text).

Sources:
- Tier 1–2 descriptions are the verbatim text from the Shadow Dark Player
  Quickstart.
- Tier 3–5 spells (not in the quickstart) use condensed descriptions based on
  the community reference at https://anjours-arsenal.net/tools/shadowdark-spells.
  Verify against the Shadow Dark core rules for exact wording.

Players can still track anything not listed here as a freeform character spell.
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
    # ---- Tier 3 (condensed from the community reference) ----
    (
        "Animate Dead", 3, WIZARD, "1 day", "Close",
        "Raise a humanoid corpse (with three limbs and an intact head) as a "
        "zombie or skeleton under your control.",
    ),
    (
        "Command", 3, PRIEST, "Focus", "Far",
        "A creature obeys a one-word command while you focus. It may resist if "
        "the command would cause it direct harm.",
    ),
    (
        "Dispel Magic", 3, WIZARD, "Instant", "Near",
        "End one spell affecting a target you can see in range.",
    ),
    (
        "Fabricate", 3, WIZARD, "10 rounds", "Near",
        "Convert tree-sized raw materials into a finished work. It reverts when "
        "the spell ends.",
    ),
    (
        "Fireball", 3, WIZARD, "Instant", "Far",
        "Hurl a bead of flame that erupts into a fiery blast, dealing 4d6 "
        "damage in a near-sized cube.",
    ),
    (
        "Fly", 3, WIZARD, "5 rounds", "Self",
        "You fly a near distance per turn and can hover.",
    ),
    (
        "Gaseous Form", 3, WIZARD, "10 rounds", "Self",
        "You turn to smoke with your gear, able to fly and pass through gaps. "
        "You can't cast spells in this form.",
    ),
    (
        "Illusion", 3, WIZARD, "Focus", "Far",
        "Create a visible and audible illusion in a near-sized cube. Creatures "
        "that believe it react to it as real.",
    ),
    (
        "Lay To Rest", 3, PRIEST, "Instant", "Close",
        "Destroy one undead creature of LV 9 or less that you touch.",
    ),
    (
        "Lightning Bolt", 3, WIZARD, "Instant", "Far",
        "A blue-white ray strikes creatures in a straight line out to far "
        "distance for 3d6 damage.",
    ),
    (
        "Magic Circle", 3, WIZARD, "Focus", "Near",
        "A circle of runes prevents a chosen creature type from attacking, "
        "casting hostile spells at, or compelling those inside.",
    ),
    (
        "Mass Cure", 3, PRIEST, "Instant", "Near",
        "All allies within near range regain 2d6 hit points.",
    ),
    (
        "Protection From Energy", 3, WIZARD, "10 minutes", "Close",
        "A target becomes immune to a chosen damage type: fire, cold, or "
        "electricity.",
    ),
    (
        "Rebuke Unholy", 3, PRIEST, "Instant", "Near",
        "Present a holy symbol; demons, devils, and outsiders (or angels and "
        "wild creatures) must flee or be destroyed.",
    ),
    (
        "Restoration", 3, PRIEST, "Instant", "Close",
        "End one curse, illness, or affliction affecting a target creature.",
    ),
    (
        "Sending", 3, WIZARD, "Instant", "Unlimited",
        "Send a brief mental message to a familiar creature on the same plane.",
    ),
    (
        "Speak With Dead", 3, BOTH, "Instant", "Close",
        "A dead body answers up to three yes/no questions truthfully. Critical "
        "failure if cast on it twice in 24 hours.",
    ),
    # ---- Tier 4 (condensed from the community reference) ----
    (
        "Arcane Eye", 4, WIZARD, "Focus", "Near",
        "Conjure an invisible eye you see through; it can fly and squeeze "
        "through narrow openings.",
    ),
    (
        "Cloudkill", 4, WIZARD, "5 rounds", "Far",
        "A putrid poison cloud blinds creatures and deals 2d6 damage per turn; "
        "it kills LV 9 or less creatures inside.",
    ),
    (
        "Commune", 4, PRIEST, "Instant", "Self",
        "Ask your deity up to three yes/no questions for truthful answers. "
        "Critical failure if cast twice in 24 hours.",
    ),
    (
        "Confusion", 4, WIZARD, "Focus", "Near",
        "A target can't take actions and moves randomly. LV 9+ creatures may "
        "resist each turn.",
    ),
    (
        "Control Water", 4, BOTH, "Focus", "Far",
        "Move and shape water up to 100 feet in width and depth — change its "
        "direction or defy gravity.",
    ),
    (
        "Dimension Door", 4, WIZARD, "Instant", "Self",
        "Teleport yourself and one willing creature to a visible location.",
    ),
    (
        "Divination", 4, WIZARD, "Instant", "Self",
        "Ask one yes/no question via casting bones or stargazing. Critical "
        "failure if cast twice in 24 hours.",
    ),
    (
        "Flame Strike", 4, PRIEST, "Instant", "Far",
        "A pillar of holy fire immolates one visible creature for 2d6 damage.",
    ),
    (
        "Passwall", 4, WIZARD, "5 rounds", "Close",
        "A tunnel of your height opens in a barrier, extending a near distance "
        "in a straight line.",
    ),
    (
        "Pillar Of Salt", 4, PRIEST, "Focus", "Near",
        "Transform a LV 5 or less creature into a salt statue; permanent after "
        "3 consecutive rounds of focus.",
    ),
    (
        "Polymorph", 4, WIZARD, "10 rounds", "Touch",
        "Transform a touched creature into a natural creature of equal or "
        "smaller size; it keeps its non-physical stats.",
    ),
    (
        "Regenerate", 4, PRIEST, "Focus", "Close",
        "A target regains 1d4 hit points on your turn and regrows lost body "
        "parts.",
    ),
    (
        "Resilient Sphere", 4, WIZARD, "5 rounds", "Close",
        "A weightless, glassy sphere extends out to close range; nothing passes "
        "through or crushes it.",
    ),
    (
        "Stoneskin", 4, WIZARD, "10 rounds", "Self",
        "Your skin becomes granite; your armor class becomes 17 (20 on a "
        "critical spellcasting check).",
    ),
    (
        "Telekinesis", 4, WIZARD, "Focus", "Far",
        "Lift and move a 1,000-pound-or-less creature or object in any "
        "direction, or hold it in place.",
    ),
    (
        "Wall of Force", 4, WIZARD, "5 rounds", "Near",
        "Conjure a transparent wall of force covering a near-sized area; "
        "nothing passes through.",
    ),
    (
        "Wrath", 4, PRIEST, "10 rounds", "Self",
        "Your weapons become magical +2 and deal an additional d8 damage.",
    ),
    # ---- Tier 5 (condensed from the community reference) ----
    (
        "Antimagic Shell", 5, WIZARD, "Focus", "Self",
        "An invisible cube of null-magic centered on you blocks all "
        "spellcasting and magical effects within it.",
    ),
    (
        "Create Undead", 5, WIZARD, "1 day", "Close",
        "Summon a wight or wraith under your control; it acts on your turn.",
    ),
    (
        "Disintegrate", 5, WIZARD, "Instant", "Far",
        "A green ray destroys a LV 5 or less creature instantly, or deals 3d8 "
        "to higher levels; it obliterates nonmagical objects.",
    ),
    (
        "Divine Vengeance", 5, PRIEST, "10 rounds", "Self",
        "Become a holy avatar with flight, magical weapons, and +4 to attack "
        "and damage.",
    ),
    (
        "Dominion", 5, PRIEST, "10 rounds", "Near",
        "Summon a combined 16 levels or less of beings (demons or angels, by "
        "your alignment) to aid you.",
    ),
    (
        "Heal", 5, PRIEST, "Instant", "Close",
        "Restore a touched creature to full hit points. You can't recast it "
        "until after a rest.",
    ),
    (
        "Hold Monster", 5, WIZARD, "Focus", "Near",
        "Paralyze one creature. LV 9+ creatures may resist each turn.",
    ),
    (
        "Judgment", 5, PRIEST, "5 rounds", "Close",
        "Banish an intelligent creature of LV 10 or less to face your god's "
        "judgment; it returns healed or damaged.",
    ),
    (
        "Plane Shift", 5, BOTH, "Instant", "Close",
        "Transport yourself and willing creatures within close range to another "
        "plane.",
    ),
    (
        "Power Word Kill", 5, WIZARD, "Instant", "Near",
        "Speak a Word of Doom; a LV 9 or less creature that hears you dies. "
        "Critical failure gives disadvantage.",
    ),
    (
        "Prismatic Orb", 5, WIZARD, "Instant", "Far",
        "An energy orb deals 3d8 damage (6d8 if anathema to the target) with a "
        "concussive blast.",
    ),
    (
        "Prophecy", 5, PRIEST, "Instant", "Self",
        "Ask one question for truthful divine guidance. Requires penance to "
        "recast.",
    ),
    (
        "Scrying", 5, WIZARD, "Focus", "Self",
        "Observe a distant creature or location on the same plane via a crystal "
        "ball or pool (DC 18 for unfamiliar targets).",
    ),
    (
        "Shapechange", 5, WIZARD, "Focus", "Self",
        "Transform into a natural creature of LV 10 or less you've seen; gain "
        "its physical stats, keep your mental stats.",
    ),
    (
        "Summon Extraplanar", 5, WIZARD, "Focus", "Near",
        "Summon an elemental or outsider of LV 7 or less under your control; it "
        "turns hostile if you lose focus.",
    ),
    (
        "Teleport", 5, WIZARD, "Instant", "Close",
        "Transport yourself and willing creatures to a known sigil or visited "
        "location; 50% chance off-target otherwise.",
    ),
    (
        "Wish", 5, WIZARD, "Instant", "Self",
        "Alter reality with a single wish, interpreted by the GM. Critical "
        "failure gives disadvantage.",
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
