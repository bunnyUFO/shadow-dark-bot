"""Tests for services.characters — the character-sheet domain operations,
exercised directly against the database with no Discord objects involved."""

import pytest

from shadowdark_bot import storage
from shadowdark_bot.db import session_scope
from shadowdark_bot.models import (
    ITEM_TYPE_COMMON,
    ITEM_TYPE_MAGICAL,
    LOCATION_ROLE_HELD,
    LOCATION_ROLE_STASH,
    Borrow,
    CharacterSpell,
    Item,
    Location,
    PlayerCharacter,
    Spell,
    TreasuryEntry,
)
from shadowdark_bot.services import characters
from shadowdark_bot.services.characters import CharacterError, CharacterNotFound


def _create(session, user_id="u1", name="Bob", str_score=None):
    char = characters.save_identity(session, user_id, name=name)
    if str_score is not None:
        characters.update_stats(
            session,
            user_id,
            scores=[str_score, 10, 10, 10, 10, 10],
            level=1,
            max_hp=None,
            armor_class=None,
            spell_ability=None,
        )
    return char


def _held_entries(session, user_id="u1"):
    held = storage.held_location(session, user_id)
    return {e.display_name: e for e in storage.location_entries(session, held.id)}


# ---------- parsing ----------


def test_parse_scores_validates_count_numbers_and_range():
    assert characters.parse_scores("14, 12 13 10 8 15") == [14, 12, 13, 10, 8, 15]
    with pytest.raises(ValueError, match="exactly six"):
        characters.parse_scores("1 2 3")
    with pytest.raises(ValueError, match="not a whole number"):
        characters.parse_scores("14 12 x 10 8 15")
    with pytest.raises(ValueError, match="out of range"):
        characters.parse_scores("14 12 31 10 8 15")


def test_parse_spellcasting_and_round_trip_format():
    assert characters.parse_spellcasting("") == (None, 0)
    assert characters.parse_spellcasting("none") == (None, 0)
    assert characters.parse_spellcasting("WIS +1") == ("wis", 1)
    assert characters.parse_spellcasting("int-2") == ("int", -2)
    with pytest.raises(ValueError):
        characters.parse_spellcasting("CHA")
    char = PlayerCharacter(spell_ability="wis", spell_check_bonus=1)
    assert characters.format_spellcasting(char) == "WIS +1"


def test_parse_alignment_and_class_title():
    assert characters.parse_alignment("chaotic") == "Chaotic"
    assert characters.parse_alignment(" none ") is None
    assert characters.parse_alignment(None) is None
    with pytest.raises(ValueError):
        characters.parse_alignment("evil")
    assert characters.parse_class_title("Wizard, Channeler") == ("Wizard", "Channeler")
    assert characters.parse_class_title("Thief") == ("Thief", None)
    assert characters.parse_class_title("") == (None, None)


# ---------- identity / stats / text fields ----------


def test_save_identity_creates_character_and_locations(dbsession):
    with session_scope() as s:
        characters.save_identity(
            s, "u1", name="  Bob  ", char_class="Fighter", alignment="lawful",
            ancestry="", background="Soldier",
        )
    with session_scope() as s:
        char = characters.load_character(s, "u1")
        assert char.name == "Bob"
        assert char.char_class == "Fighter"
        assert char.alignment == "Lawful"
        assert char.ancestry is None  # blank → None
        assert storage.held_location(s, "u1").name == "Bob"
        assert storage.stash_location(s, "u1").name == "Bob Guild Stash"


def test_save_identity_updates_and_renames_locations(dbsession):
    with session_scope() as s:
        _create(s)
    with session_scope() as s:
        characters.save_identity(s, "u1", name="Bobby")
    with session_scope() as s:
        assert s.query(PlayerCharacter).count() == 1
        assert storage.held_location(s, "u1").name == "Bobby"


def test_save_identity_rejects_blank_name_and_bad_alignment(dbsession):
    with session_scope() as s:
        with pytest.raises(CharacterError, match="Name cannot be empty"):
            characters.save_identity(s, "u1", name="  ")
        with pytest.raises(CharacterError, match="Alignment must be"):
            characters.save_identity(s, "u1", name="Bob", alignment="evil")


def test_update_stats_syncs_held_capacity(dbsession):
    with session_scope() as s:
        _create(s, str_score=16)
    with session_scope() as s:
        char = characters.load_character(s, "u1")
        assert char.str_score == 16
        assert storage.held_location(s, "u1").max_gear_slots == 16


def test_update_stats_validation(dbsession):
    base = dict(level=1, max_hp=None, armor_class=None, spell_ability=None)
    with session_scope() as s:
        _create(s)
        with pytest.raises(CharacterError, match="out of range"):
            characters.update_stats(s, "u1", scores=[0, 10, 10, 10, 10, 10], **base)
        with pytest.raises(CharacterError, match="Level"):
            characters.update_stats(
                s, "u1", scores=[10] * 6, **{**base, "level": 0}
            )
        with pytest.raises(CharacterError, match="Spellcasting"):
            characters.update_stats(
                s, "u1", scores=[10] * 6, **{**base, "spell_ability": "cha"}
            )
    with session_scope() as s, pytest.raises(CharacterNotFound):
        characters.update_stats(s, "nobody", scores=[10] * 6, **base)


def test_set_gold_and_text_fields(dbsession):
    with session_scope() as s:
        _create(s)
        characters.set_gold(s, "u1", 1234)
        characters.set_proficiencies(s, "u1", "  Longswords ")
        characters.set_skills_knowledge(
            s, "u1", languages="Common, Elvish", talents="", additional_info=None
        )
    with session_scope() as s:
        char = characters.load_character(s, "u1")
        assert char.gold_cp == 1234
        assert char.proficiencies == "Longswords"
        assert char.languages == "Common, Elvish"
        assert char.talents is None
        characters.set_gold(s, "u1", None)
        assert char.gold_cp == 0
        with pytest.raises(CharacterError, match="negative"):
            characters.set_gold(s, "u1", -1)


# ---------- carry / add more / remove ----------


def _seed_items(session):
    session.add_all([
        Item(name="Rope", gear_slots=1, bundle_size=1, item_type=ITEM_TYPE_COMMON),
        Item(name="Arrow", gear_slots=1, bundle_size=20, item_type=ITEM_TYPE_COMMON),
    ])
    session.flush()


def test_carry_catalog_and_freeform(dbsession):
    with session_scope() as s:
        _create(s, str_score=15)
        _seed_items(s)
        r = characters.carry(s, "u1", item_name=" Arrow ", quantity=25)
        assert (r.display_name, r.location_name) == ("Arrow", "Bob")
        assert (r.used_slots, r.max_slots) == (2, 15)  # 25 arrows = 2 bundles
        characters.carry(s, "u1", item_name="Idol", quantity=1,
                         freeform_slots=0.5, notes="Creepy")
        r = characters.carry(s, "u1", item_name="Rope", quantity=1,
                             role=LOCATION_ROLE_STASH)
        assert r.location_name == "Bob Guild Stash"
    with session_scope() as s:
        held = _held_entries(s)
        assert held["Arrow"].quantity == 25
        assert held["Idol"].notes == "Creepy"
        assert "Rope" not in held


def test_carry_rejects_over_capacity_and_bad_input(dbsession):
    with session_scope() as s:
        _create(s, str_score=10)
        _seed_items(s)
        with pytest.raises(CharacterError, match="needs?.*11 more"):
            characters.carry(s, "u1", item_name="Rope", quantity=11)
        with pytest.raises(CharacterError, match="empty"):
            characters.carry(s, "u1", item_name=" ", quantity=1)
        with pytest.raises(CharacterError, match="Quantity"):
            characters.carry(s, "u1", item_name="Rope", quantity=0)
        with pytest.raises(CharacterError, match="Gear slots"):
            characters.carry(s, "u1", item_name="Gem", quantity=1, freeform_slots=-1)
        with pytest.raises(CharacterNotFound):
            characters.carry(s, "nobody", item_name="Rope", quantity=1)


def test_failed_carry_rolls_back(dbsession):
    with session_scope() as s:
        _create(s, str_score=10)
        _seed_items(s)
    with pytest.raises(CharacterError), session_scope() as s:
        characters.carry(s, "u1", item_name="Rope", quantity=11)
    with session_scope() as s:
        assert _held_entries(s) == {}


def test_add_more_and_remove(dbsession):
    with session_scope() as s:
        _create(s, str_score=10)
        _seed_items(s)
        characters.carry(s, "u1", item_name="Rope", quantity=2)
        eid = _held_entries(s)["Rope"].id
        r = characters.add_more(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid,
                                quantity=3)
        assert (r.display_name, r.used_slots) == ("Rope", 5)
        with pytest.raises(CharacterError, match="would need 6 more"):
            characters.add_more(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid,
                                quantity=6)
        with pytest.raises(CharacterError, match="only have 5"):
            characters.remove(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid,
                              quantity=6)
        r = characters.remove(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid,
                              quantity=2)
        assert r.removed == 2 and r.returned_treasury_ids == []
        assert _held_entries(s)["Rope"].quantity == 3
        r = characters.remove(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid)
        assert r.removed == 3
        assert _held_entries(s) == {}
        with pytest.raises(CharacterError, match="already gone"):
            characters.remove(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid)


def test_cannot_touch_another_characters_stack(dbsession):
    with session_scope() as s:
        _create(s, "u1", "Bob")
        _create(s, "u2", "Amy")
        _seed_items(s)
        characters.carry(s, "u2", item_name="Rope", quantity=1)
        amy_eid = _held_entries(s, "u2")["Rope"].id
        assert characters.location_entry(s, "u1", LOCATION_ROLE_HELD, amy_eid) is None
        with pytest.raises(CharacterError):
            characters.remove(s, "u1", role=LOCATION_ROLE_HELD, entry_id=amy_eid)
        with pytest.raises(CharacterError):
            characters.add_more(s, "u1", role=LOCATION_ROLE_HELD, entry_id=amy_eid,
                                quantity=1)


def test_remove_returns_borrowed_copies_to_treasury(dbsession):
    with session_scope() as s:
        _create(s)
        amulet = Item(name="Amulet", gear_slots=1, item_type=ITEM_TYPE_MAGICAL)
        vault = Location(name="Vault", kind="treasury", max_gear_slots=None)
        s.add_all([amulet, vault])
        s.flush()
        te = TreasuryEntry(location_id=vault.id, item_id=amulet.id, status="borrowed")
        s.add(te)
        s.flush()
        s.add(Borrow(treasury_entry_id=te.id, borrower_id="u1"))
        held = storage.held_location(s, "u1")
        stack = storage.add_stack(s, held, quantity=1, item=amulet)
        s.flush()
        r = characters.remove(s, "u1", role=LOCATION_ROLE_HELD, entry_id=stack.id)
        assert r.returned_treasury_ids == [te.id]
        assert te.status == "available"


# ---------- give ----------


def test_give_between_characters_and_into_guild(dbsession):
    with session_scope() as s:
        _create(s, "u1", "Bob")
        _create(s, "u2", "Amy")
        _seed_items(s)
        armory = Location(name="Armory", kind="inventory", max_gear_slots=1)
        s.add(armory)
        s.flush()
        characters.carry(s, "u1", item_name="Rope", quantity=3)
        eid = _held_entries(s)["Rope"].id
        amy_held = storage.held_location(s, "u2")

        r = characters.give(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid,
                            target_location_id=amy_held.id, quantity=1)
        assert (r.display_name, r.quantity, r.target_name) == ("Rope", 1, "Amy")
        assert _held_entries(s, "u2")["Rope"].quantity == 1

        with pytest.raises(CharacterError, match="Armory.*would need 2 more"):
            characters.give(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid,
                            target_location_id=armory.id)
        r = characters.give(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid,
                            target_location_id=armory.id, quantity=1)
        assert r.target_name == "Armory"


def test_give_blocks_other_stash_and_lists_targets(dbsession):
    with session_scope() as s:
        _create(s, "u1", "Bob")
        _create(s, "u2", "Amy")
        _seed_items(s)
        s.add(Location(name="Armory", kind="inventory", max_gear_slots=50))
        s.flush()
        characters.carry(s, "u1", item_name="Rope", quantity=1)
        eid = _held_entries(s)["Rope"].id
        amy_stash = storage.stash_location(s, "u2")
        with pytest.raises(CharacterError, match="another character's stash"):
            characters.give(s, "u1", role=LOCATION_ROLE_HELD, entry_id=eid,
                            target_location_id=amy_stash.id)

        held = storage.held_location(s, "u1")
        names = [loc.name for loc in characters.give_target_locations(s, "u1", held)]
        assert names[0] == "Armory"  # guild locations first
        assert "Bob Guild Stash" in names and "Amy" in names
        assert "Amy Guild Stash" not in names and "Bob" not in names


# ---------- spells ----------


def _seed_spells(session):
    session.add_all([
        Spell(name="Magic Missile", tier=1, classes="wizard"),
        Spell(name="Cure Wounds", tier=1, classes="priest"),
        Spell(name="Light", tier=1, classes="priest,wizard"),
        Spell(name="Fireball", tier=3, classes="wizard"),
    ])
    session.flush()


def _make_wizard(session):
    _create(session)
    characters.update_stats(
        session, "u1", scores=[10] * 6, level=1, max_hp=None, armor_class=None,
        spell_ability="int",
    )


def test_learn_and_forget_spells(dbsession):
    with session_scope() as s:
        _seed_spells(s)
        _make_wizard(s)
        char = characters.load_character(s, "u1")
        assert characters.spell_class_for(char) == "wizard"
        assert [sp.name for sp in characters.addable_spells(s, char, "wizard")] == [
            "Light", "Magic Missile", "Fireball"
        ]
        characters.learn_spell(s, "u1", "Fireball")  # any tier is learnable
        with pytest.raises(CharacterError, match="already know"):
            characters.learn_spell(s, "u1", "Fireball")
        with pytest.raises(CharacterError, match="Priest spell"):
            characters.learn_spell(s, "u1", "Cure Wounds")
        with pytest.raises(CharacterError, match="no longer exists"):
            characters.learn_spell(s, "u1", "Wish")
    with session_scope() as s:
        char = characters.load_character(s, "u1")
        [cs] = char.spells
        assert characters.forget_spell(s, "u1", cs.id) == "Fireball"
    with session_scope() as s:
        assert s.query(CharacterSpell).count() == 0
        with pytest.raises(CharacterError, match="don't know"):
            characters.forget_spell(s, "u1", 999)


def test_non_caster_cannot_learn(dbsession):
    with session_scope() as s:
        _seed_spells(s)
        _create(s)
        with pytest.raises(CharacterError, match="isn't a spellcaster"):
            characters.learn_spell(s, "u1", "Light")


# ---------- delete ----------


def test_delete_preview_and_delete(dbsession):
    with session_scope() as s:
        _create(s)
        _seed_items(s)
        characters.carry(s, "u1", item_name="Rope", quantity=1)
        characters.carry(s, "u1", item_name="Arrow", quantity=1,
                         role=LOCATION_ROLE_STASH)
        preview = characters.delete_preview(s, "u1")
        assert (preview.name, preview.stack_count) == ("Bob", 2)
    with session_scope() as s:
        assert characters.delete_character(s, "u1") is True
    with session_scope() as s:
        assert characters.load_character(s, "u1") is None
        assert storage.held_location(s, "u1") is None
        assert characters.delete_character(s, "u1") is False
        with pytest.raises(CharacterNotFound):
            characters.delete_preview(s, "u1")
