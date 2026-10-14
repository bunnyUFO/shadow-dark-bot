"""Effects (duration tracker + temporary stat changes) and lost spells,
exercised against the services and the sheet builder."""

import pytest

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import Spell
from shadowdark_bot.services import characters
from shadowdark_bot.services.characters import CharacterError, CharacterNotFound
from shadowdark_bot.web.schemas import character_sheet


def _make(session, user_id="u1"):
    characters.save_identity(session, user_id, name="Bob")
    # DEX 14 -> +2; base AC 11 -> sheet AC 13. STR 15 -> +2.
    characters.update_stats(
        session, user_id, scores=[15, 14, 10, 16, 10, 10], level=1, max_hp=8,
        armor_class=11, spell_ability="int",
    )


def _sheet(session, user_id="u1"):
    char = characters.require_character(session, user_id)
    return character_sheet(session, char, include_stash=True)


def test_add_tick_untick_and_bounds(dbsession):
    with session_scope() as s:
        _make(s)
        e = characters.add_effect(s, "u1", " Light ", 6)
        assert (e.name, e.max_rounds, e.rounds_used) == ("Light", 6, 0)
        characters.update_effect(s, "u1", e.id, rounds_used=3)
        assert e.rounds_used == 3 and not e.ended
        characters.update_effect(s, "u1", e.id, rounds_used=6)
        assert e.ended
        characters.update_effect(s, "u1", e.id, rounds_used=0)
        for bad in (-1, 7):
            with pytest.raises(CharacterError):
                characters.update_effect(s, "u1", e.id, rounds_used=bad)
        # shrinking the duration clamps the ticks; dropping it clears them
        characters.update_effect(s, "u1", e.id, rounds_used=5)
        characters.update_effect(s, "u1", e.id, max_rounds=2)
        assert e.rounds_used == 2
        characters.update_effect(s, "u1", e.id, max_rounds=None)
        assert e.max_rounds is None and e.rounds_used == 0 and not e.ended
        with pytest.raises(CharacterError):
            characters.update_effect(s, "u1", e.id, rounds_used=1)


def test_effect_validation(dbsession):
    with session_scope() as s:
        _make(s)
        for name, rounds in (("", 5), ("  ", 5), ("x", 0), ("x", 101)):
            with pytest.raises(CharacterError):
                characters.add_effect(s, "u1", name, rounds)
        with pytest.raises(CharacterError):
            characters.add_effect(s, "u1", "x", 5, {"ac": "lots"})
        with pytest.raises(CharacterError):
            characters.add_effect(s, "u1", "x", 5, {"ac": "+11"})
        with pytest.raises(CharacterError):
            characters.add_effect(s, "u1", "x", 5, {"luck": "+1"})
        e = characters.add_effect(s, "u1", "ok", None, {"ac": "3", "str": " "})
        assert e.bonus_map() == {"ac": "+3"}


def test_remove_and_other_users_effect(dbsession):
    with session_scope() as s:
        _make(s)
        _make(s, "u2")
        e = characters.add_effect(s, "u1", "Light", 6)
        with pytest.raises(CharacterNotFound):
            characters.remove_effect(s, "u2", e.id)
        with pytest.raises(CharacterNotFound):
            characters.update_effect(s, "u2", e.id, name="Mine now")
        assert characters.remove_effect(s, "u1", e.id) == "Light"
        assert characters.require_character(s, "u1").effects == []


def test_add_bonus_applies_and_leaves_base_alone(dbsession):
    with session_scope() as s:
        _make(s)
        e = characters.add_effect(s, "u1", "Mage Armor", None, {"ac": "+3"})
        sheet = _sheet(s)
        assert sheet.armor_class_base == 11 and sheet.armor_class == 13
        ac = sheet.effective["ac"]
        assert (ac.base, ac.total, ac.boosted) == (13, 16, True)
        assert ac.sources == [{"effect": "Mage Armor", "change": "+3"}]
        assert sheet.effective["str"].boosted is False
        # removing the effect removes the bonus
        characters.remove_effect(s, "u1", e.id)
        ac = _sheet(s).effective["ac"]
        assert (ac.total, ac.boosted) == (13, False)
        assert characters.require_character(s, "u1").armor_class == 11


def test_bonuses_sum_and_ended_effects_do_not_apply(dbsession):
    with session_scope() as s:
        _make(s)
        a = characters.add_effect(s, "u1", "Shield of Faith", 5, {"ac": "+2", "hp": "+4"})
        characters.add_effect(s, "u1", "Cursed", None, {"ac": "-1", "str": "-2"})
        sheet = _sheet(s)
        assert sheet.effective["ac"].total == 13 + 2 - 1
        assert sheet.effective["hp"].total == 12 and sheet.max_hp == 8
        assert sheet.effective["str"].total == 0 and sheet.abilities[0].modifier == 2
        characters.update_effect(s, "u1", a.id, rounds_used=5)  # ended
        sheet = _sheet(s)
        assert sheet.effective["ac"].total == 12
        assert sheet.effective["hp"].total == 8 and not sheet.effective["hp"].boosted
        assert [e.ended for e in sheet.effects] == [True, False]


def test_set_mode(dbsession):
    with session_scope() as s:
        _make(s)
        a = characters.add_effect(s, "u1", "Mage Armor", 10, {"ac": "=14"})
        ac = _sheet(s).effective["ac"]
        assert (ac.base, ac.total) == (13, 14)
        assert ac.sources == [{"effect": "Mage Armor", "change": "=14"}]
        # set + add: the add lands on top of the set value
        characters.add_effect(s, "u1", "Shield of Faith", None, {"ac": "+1"})
        assert _sheet(s).effective["ac"].total == 15
        # two sets: the highest wins
        characters.add_effect(s, "u1", "Other", None, {"ac": "=16"})
        assert _sheet(s).effective["ac"].total == 17
        # an ended set is ignored
        characters.update_effect(s, "u1", a.id, rounds_used=10)
        assert _sheet(s).effective["ac"].total == 17
        characters.remove_effect(s, "u1", [e for e in characters.require_character(s, "u1").effects if e.name == "Other"][0].id)
        assert _sheet(s).effective["ac"].total == 14  # base 13 + 1


def test_dex_bonus_flows_into_ac_unless_ac_is_set(dbsession):
    with session_scope() as s:
        _make(s)
        characters.add_effect(s, "u1", "Haste", None, {"dex": "+2"})
        sheet = _sheet(s)
        assert sheet.effective["dex"].total == 4
        assert sheet.effective["ac"].total == 15 and sheet.effective["ac"].boosted
        characters.add_effect(s, "u1", "Mage Armor", None, {"ac": "=14"})
        assert _sheet(s).effective["ac"].total == 14


def test_ability_set_mode(dbsession):
    with session_scope() as s:
        _make(s)
        characters.add_effect(s, "u1", "Bull's Strength", None, {"str": "=4"})
        sheet = _sheet(s)
        assert sheet.effective["str"].total == 4
        assert sheet.abilities[0].score == 15 and sheet.abilities[0].modifier == 2


def test_lost_spell_toggle_and_rest(dbsession):
    with session_scope() as s:
        s.add_all([
            Spell(name="Light", tier=1, classes="priest,wizard"),
            Spell(name="Sleep", tier=1, classes="wizard"),
        ])
        _make(s)
        _make(s, "u2")
        characters.learn_spell(s, "u1", "Light")
        characters.learn_spell(s, "u1", "Sleep")
        characters.learn_spell(s, "u2", "Light")
        char = characters.require_character(s, "u1")
        light, sleep = (characters.sorted_spells(char))
        theirs = characters.require_character(s, "u2").spells[0]
        with pytest.raises(CharacterNotFound):
            characters.set_spell_lost(s, "u1", theirs.id, True)
        characters.set_spell_lost(s, "u1", light.id, True)
        characters.set_spell_lost(s, "u1", sleep.id, True)
        assert [k.lost for k in _sheet(s).spells] == [True, True]
        assert characters.rest(s, "u1") == 2
        assert [k.lost for k in _sheet(s).spells] == [False, False]
        assert theirs.lost is False
