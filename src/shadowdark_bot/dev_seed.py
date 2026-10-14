"""Sample party + guild data for the dev runner (`python -m shadowdark_bot.dev`).

Characters go through `services.characters`, so the same rules the app enforces
(capacity, class, ranges) apply to the seed. The user ids are 18-digit
snowflake-like strings (> 2**53) on purpose: they catch frontend code that
turns ids into JS numbers. Later milestones reuse this data; keep it stable.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from shadowdark_bot import storage
from shadowdark_bot.models import (
    LOCATION_ROLE_HELD,
    LOCATION_ROLE_STASH,
    Borrow,
    Coffer,
    Creature,
    Item,
    Location,
    PlayerCharacter,
    Spell,
    TreasuryEntry,
)
from shadowdark_bot.services import characters, creatures, effects

BRANNOC_ID = "900000000000000001"
YSOLDE_ID = "900000000000000002"
ALDRIC_ID = "900000000000000003"

# (name, type, gear_slots, bundle_size, value_cp, description)
CATALOG: list[tuple[str, str, float, int, int | None, str | None]] = [
    ("Longsword", "weapon", 1, 1, 900, None),
    ("Dagger", "weapon", 1, 1, 100, None),
    ("Shortbow", "weapon", 1, 1, 600, None),
    ("Arrow", "common", 1, 20, 5, None),
    ("Chainmail", "armor", 2, 1, 6000, None),
    ("Leather Armor", "armor", 1, 1, 1000, None),
    ("Shield", "armor", 1, 1, 1000, None),
    ("Torch", "common", 1, 1, 50, None),
    ("Rations", "common", 1, 3, 50, "Dried meat and hard bread"),
    ("Rope", "common", 1, 1, 100, "60 feet of hemp"),
    ("Crowbar", "common", 1, 1, 50, None),
    ("Flask of Oil", "common", 1, 1, 50, None),
    ("Scroll of Light", "scroll", 1, 1, 2500, None),
    ("Potion of Healing", "potion", 1, 1, 5000, None),
    ("Potion of Giant Strength", "potion", 1, 1, 15000, "Your muscles swell for a short while."),
    ("Ring of Feather Falling", "magical", 1, 1, None, None),
    ("Wand of Sleep", "magical", 1, 1, None, None),
    ("Cloak of Elvenkind", "magical", 1, 1, None, None),
]


@dataclass
class _Sample:
    user_id: str
    name: str
    char_class: str
    title: str | None
    ancestry: str
    alignment: str
    background: str | None
    level: int
    scores: tuple[int, ...]
    max_hp: int
    armor_class: int
    spell_ability: str | None
    gold_cp: int
    held: list[tuple[str, int]] = field(default_factory=list)
    # (name, quantity, slots each, notes)
    freeform: list[tuple[str, int, float, str | None]] = field(default_factory=list)
    stash: list[tuple[str, int]] = field(default_factory=list)
    spells: list[str] = field(default_factory=list)
    lost_spells: list[str] = field(default_factory=list)
    # (name, max rounds, rounds used, bonuses)
    effects: list[tuple[str, int | None, int, dict]] = field(default_factory=list)
    proficiencies: str | None = None
    languages: str | None = None
    talents: str | None = None
    additional_info: str | None = None
    xp: int = 0


PARTY = [
    _Sample(
        BRANNOC_ID, "Brannoc Ashfall", "Fighter", "Warrior", "Human", "Lawful",
        "Soldier", 3, (16, 12, 14, 9, 10, 11), 22, 15, None, 2550,
        held=[
            ("Longsword", 1), ("Shield", 1), ("Chainmail", 1), ("Shortbow", 1),
            ("Arrow", 25), ("Torch", 3), ("Rations", 3),
        ],
        freeform=[("Lucky Coin", 1, 0.0, "Won from a ghoul at dice")],
        stash=[("Rope", 1), ("Crowbar", 1)],
        proficiencies="All weapons; all armor and shields",
        languages="Common",
        talents="Weapon mastery (longsword)",
        additional_info="Owes the Ragged Hound 3 gp.",
        xp=12,
    ),
    _Sample(
        YSOLDE_ID, "Ysolde Vane", "Wizard", "Apprentice", "Elf", "Neutral",
        "Scholar", 2, (9, 14, 12, 17, 11, 10), 6, 11, "int", 850,
        held=[
            ("Dagger", 1), ("Torch", 2), ("Rations", 3), ("Flask of Oil", 2),
            ("Scroll of Light", 1),
        ],
        freeform=[("Spellbook", 1, 1.0, "Bound in grey leather")],
        stash=[("Potion of Healing", 1)],
        spells=["Magic Missile", "Sleep", "Light", "Detect Magic", "Misty Step",
                "Mage Armor"],
        lost_spells=["Sleep"],
        effects=[("Light", 6, 2, {})],
        languages="Common, Elvish, Draconic",
        xp=20,
    ),
    _Sample(
        ALDRIC_ID, "Brother Aldric", "Priest", None, "Dwarf", "Lawful",
        None, 1, (12, 8, 13, 10, 15, 10), 7, 13, "wis", 0,
        held=[("Leather Armor", 1), ("Torch", 1)],
        freeform=[("Holy Symbol", 1, 1.0, None)],
        spells=["Cure Wounds", "Protection From Evil", "Turn Undead"],
        xp=3,
    ),
]


def _seed_character(session: Session, s: _Sample) -> None:
    characters.save_identity(
        session, s.user_id, name=s.name, char_class=s.char_class, title=s.title,
        ancestry=s.ancestry, alignment=s.alignment, background=s.background,
    )
    # Stats first: STR sets the held capacity, and spells need spell_ability.
    characters.update_stats(
        session, s.user_id, scores=s.scores, level=s.level, max_hp=s.max_hp,
        armor_class=s.armor_class, spell_ability=s.spell_ability, xp=s.xp,
    )
    characters.set_gold(session, s.user_id, s.gold_cp)
    characters.set_proficiencies(session, s.user_id, s.proficiencies)
    characters.set_skills_knowledge(
        session, s.user_id, languages=s.languages, talents=s.talents,
        additional_info=s.additional_info,
    )
    for item, qty in s.held:
        characters.carry(session, s.user_id, item_name=item, quantity=qty)
    for name, qty, slots, notes in s.freeform:
        characters.carry(
            session, s.user_id, item_name=name, quantity=qty,
            freeform_slots=slots, notes=notes,
        )
    for item, qty in s.stash:
        characters.carry(
            session, s.user_id, item_name=item, quantity=qty, role=LOCATION_ROLE_STASH
        )
    for spell in s.spells:
        characters.learn_spell(session, s.user_id, spell)
    known = {
        k.display_name: k for k in characters.require_character(session, s.user_id).spells
    }
    for spell in s.lost_spells:
        characters.set_spell_lost(session, s.user_id, known[spell].id, True)
    for name, rounds, used, bonuses in s.effects:
        eff = characters.add_effect(session, s.user_id, name, rounds, bonuses)
        if used:
            characters.update_effect(session, s.user_id, eff.id, rounds_used=used)


def _seed_guild(session: Session) -> None:
    items = {i.name: i for i in session.scalars(select(Item))}

    session.add(Coffer(balance_cp=15240))

    def storeroom(
        name: str, slots: float, description: str, stacks: list[tuple[str, int]]
    ) -> None:
        loc = Location(
            name=name, kind="inventory", description=description,
            max_gear_slots=slots, owner_user_id=None,
        )
        session.add(loc)
        session.flush()
        for item_name, qty in stacks:
            storage.add_stack(session, loc, quantity=qty, item=items[item_name])

    storeroom(
        "Guild Hall Storeroom", 40, "Behind the bar at the Ragged Hound",
        [("Torch", 10), ("Rations", 6), ("Rope", 2), ("Arrow", 40),
         ("Potion of Healing", 2)],
    )
    storeroom(
        "Pack Mule", 8, "Bessie. Mostly reliable.",
        [("Flask of Oil", 3), ("Crowbar", 1)],
    )

    treasury = Location(name="Treasury", kind="treasury", max_gear_slots=0)
    session.add(treasury)
    session.flush()
    ring = TreasuryEntry(
        location_id=treasury.id, item_id=items["Ring of Feather Falling"].id,
        status="borrowed",
    )
    session.add_all([
        ring,
        TreasuryEntry(
            location_id=treasury.id, item_id=items["Wand of Sleep"].id,
            tag="3 charges", status="available",
        ),
        TreasuryEntry(
            location_id=treasury.id, item_id=items["Cloak of Elvenkind"].id,
            status="available",
        ),
    ])
    session.flush()
    session.add(
        Borrow(
            treasury_entry_id=ring.id, borrower_id=BRANNOC_ID,
            notes="For the cliff descent",
        )
    )
    # Same effect as the treasury cog's borrow: the item lands in held items.
    characters.carry(
        session, BRANNOC_ID, item_name="Ring of Feather Falling", quantity=1,
        role=LOCATION_ROLE_HELD,
    )
    session.flush()


def _seed_creatures(session: Session) -> None:
    """A bestiary with one fully known, one partly known and one unknown creature."""
    today = date.today()
    goblin = creatures.create_creature(
        session, BRANNOC_ID, name="Goblin", level=1, ac=11, hp=5,
        attacks="1 club +1 (1d4)", movement="near", str_mod=-1, dex_mod=1,
        con_mod=0, int_mod=-1, wis_mod=-1, cha_mod=-2, alignment="C",
        special_abilities="Keen Senses. Can't be surprised.",
        description="Small, wiry and loud. They swarm.",
        notes="Ambushed us on the road to Highcliff.",
    )
    creatures.add_encounter(session, BRANNOC_ID, goblin.id, {
        "date": (today - timedelta(days=21)).isoformat(), "session": "Session 4",
        "place": "Highcliff road", "outcome": "Drove them off; one fled.",
    })
    creatures.add_encounter(session, YSOLDE_ID, goblin.id, {
        "date": (today - timedelta(days=7)).isoformat(), "session": "Session 6",
        "place": "The old mine", "outcome": "Killed three, took a crude map.",
    })
    creatures.create_creature(
        session, YSOLDE_ID, name="Cave crawler", level=3, ac=13,
        attacks="2 claw +3 (1d6)", movement="near (climb)", dex_mod=2, alignment="N",
        description="Pale and many-legged. Hunts by sound.",
    )
    creatures.create_creature(
        session, ALDRIC_ID, name="Pale thing in the well",
        notes="Heard it breathing. Nobody looked down.",
    )


def _seed_effects(session: Session) -> None:
    """A few library effects, each linked to something that causes it."""

    def link(template, kind, model, name):
        target = session.scalar(select(model).where(model.name == name))
        if target is not None:  # a missing target just leaves the effect unlinked
            effects.add_link(session, template.id, kind, target.id)

    mage_armor = effects.create_template(
        session, YSOLDE_ID, name="Mage Armor", max_rounds=10, bonuses={"ac": "=14"},
        description="An invisible layer of magical force. Your AC becomes 14.",
    )
    link(mage_armor, "spell", Spell, "Mage Armor")
    shield = effects.create_template(
        session, ALDRIC_ID, name="Shield of Faith", max_rounds=5, bonuses={"ac": "+2"},
        description="A shimmering field of faith turns aside blows.",
    )
    link(shield, "spell", Spell, "Shield of Faith")
    strength = effects.create_template(
        session, BRANNOC_ID, name="Giant Strength", max_rounds=10, bonuses={"str": "+3"},
        description="Drunk from a potion: your STR modifier rises for a few rounds.",
    )
    link(strength, "item", Item, "Potion of Giant Strength")
    frightened = effects.create_template(
        session, BRANNOC_ID, name="Frightened", max_rounds=3,
        bonuses={"str": "-1", "dex": "-1"},
        description="Shaken by a goblin's war cry. Fights worse for a few rounds.",
    )
    link(frightened, "creature", Creature, "Goblin")


def seed_sample_data(session: Session) -> bool:
    """Seed the sample party and guild. Returns False (writing nothing) if any
    character already exists. Needs the reference spells seeded first."""
    if session.scalar(select(PlayerCharacter.id).limit(1)) is not None:
        return False
    for name, item_type, slots, bundle, value, desc in CATALOG:
        session.add(
            Item(
                name=name, item_type=item_type, gear_slots=slots,
                bundle_size=bundle, value_cp=value, description=desc,
            )
        )
    session.flush()
    for sample in PARTY:
        _seed_character(session, sample)
    _seed_guild(session)
    _seed_creatures(session)
    _seed_effects(session)
    return True
