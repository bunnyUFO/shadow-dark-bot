"""Magic bonuses (+1..+3) on weapon and armor stacks."""

import pytest
from sqlalchemy.exc import IntegrityError
from test_web_api import _stack, api, auth, login  # noqa: F401

from shadowdark_bot import storage
from shadowdark_bot.db import session_scope
from shadowdark_bot.models import (
    ITEM_TYPE_ARMOR,
    ITEM_TYPE_COMMON,
    ITEM_TYPE_WEAPON,
    InventoryEntry,
    Item,
    Location,
)
from shadowdark_bot.services import catalog, characters, guild
from shadowdark_bot.services.characters import CharacterError
from shadowdark_bot.web.schemas import character_sheet


def _world(session):
    session.add_all([
        Item(name="Dagger", gear_slots=1, item_type=ITEM_TYPE_WEAPON, value_cp=100),
        Item(name="Leather Armor", gear_slots=1, item_type=ITEM_TYPE_ARMOR, value_cp=500),
        Item(name="Shield", gear_slots=1, item_type=ITEM_TYPE_ARMOR, value_cp=100),
        Item(name="Chainmail", gear_slots=2, item_type=ITEM_TYPE_ARMOR, value_cp=1000),
        Item(name="Rope", gear_slots=1, item_type=ITEM_TYPE_COMMON, value_cp=100),
    ])
    session.flush()
    for uid, name in (("u1", "Bob"), ("u2", "Alice")):
        characters.save_identity(session, uid, name=name)
        characters.update_stats(session, uid, scores=[10] * 6, level=1, max_hp=4, armor_class=11, spell_ability=None)
        characters.set_gold(session, uid, 10000)
    room = Location(name="Storeroom", kind="inventory", max_gear_slots=20)
    session.add(room)
    session.flush()
    return room


def _locs(session, uid="u1"):
    return storage.ensure_character_locations(session, characters.require_character(session, uid))


def _buy(session, name, bonus, qty=1, uid="u1", dest=None):
    item = session.query(Item).filter_by(name=name).one()
    dest = dest or _locs(session, uid)[0].id
    return characters.buy(
        session, uid, item_id=item.id, quantity=qty, pay_from="character",
        destination_location_id=dest, bonus=bonus,
    )


def test_buy_bonus_stack(dbsession):
    with session_scope() as s:
        _world(s)
        res = _buy(s, "Dagger", 1)
        assert res.display_name == "Dagger +1"
        assert res.total_cp == 100  # base price at any bonus
        (stack,) = storage.location_entries(s, _locs(s)[0].id)
        assert stack.bonus == 1 and stack.display_name == "Dagger +1"
        assert characters.require_character(s, "u1").gold_cp == 9900


def test_bonus_stacks_are_separate_and_merge(dbsession):
    with session_scope() as s:
        _world(s)
        _buy(s, "Dagger", 0)
        _buy(s, "Dagger", 1)
        _buy(s, "Dagger", 1)
        stacks = storage.location_entries(s, _locs(s)[0].id)
        assert [(e.display_name, e.quantity) for e in stacks] == [("Dagger", 1), ("Dagger +1", 2)]


@pytest.mark.parametrize("name,bonus", [("Rope", 1), ("Dagger", 4), ("Dagger", -1)])
def test_bad_bonus_refused(dbsession, name, bonus):
    with session_scope() as s:
        _world(s)
        with pytest.raises(CharacterError):
            _buy(s, name, bonus)
        assert characters.require_character(s, "u1").gold_cp == 10000
        assert storage.location_entries(s, _locs(s)[0].id) == []


def test_bonus_error_messages(dbsession):
    with session_scope() as s:
        _world(s)
        with pytest.raises(CharacterError, match="Only weapons and armor can have a magic bonus."):
            _buy(s, "Rope", 1)
        with pytest.raises(CharacterError, match="Magic bonus must be"):
            _buy(s, "Dagger", 4)
        with pytest.raises(CharacterError, match="Only weapons and armor"):
            characters.carry(s, "u1", item_name="Mystery", quantity=1, bonus=1)


def test_carry_by_name_with_suffix(dbsession):
    with session_scope() as s:
        _world(s)
        res = characters.carry(s, "u1", item_name="Dagger +2", quantity=1)
        assert res.display_name == "Dagger +2"
        (stack,) = storage.location_entries(s, _locs(s)[0].id)
        assert stack.bonus == 2 and stack.item_id is not None
        # a rope with a suffix is refused; an unknown base stays freeform
        with pytest.raises(CharacterError):
            characters.carry(s, "u1", item_name="Rope +1", quantity=1)
        characters.carry(s, "u1", item_name="Idol +1", quantity=1)
        entries = storage.location_entries(s, _locs(s)[0].id)
        assert any(e.name == "Idol +1" and e.bonus == 0 for e in entries)


def test_split_bonus_name():
    assert storage.split_bonus_name("Dagger +2") == ("Dagger", 2)
    assert storage.split_bonus_name("Dagger") == ("Dagger", 0)
    assert storage.split_bonus_name("Dagger +4") == ("Dagger +4", 0)
    assert storage.split_bonus_name("+1") == ("+1", 0)


def test_store_take_give_keep_bonus(dbsession):
    with session_scope() as s:
        room = _world(s)
        _buy(s, "Dagger", 1)
        _buy(s, "Dagger", 0)
        held = _locs(s)[0]
        plus = next(e for e in storage.location_entries(s, held.id) if e.bonus == 1)
        guild.store(s, "u1", location_id=room.id, items=[("held", plus.id, None)])
        (in_room,) = storage.location_entries(s, room.id)
        assert in_room.bonus == 1
        assert [e.bonus for e in storage.location_entries(s, held.id)] == [0]
        guild.take(s, "u1", location_id=room.id, entry_id=in_room.id)
        stacks = storage.location_entries(s, held.id)
        assert sorted((e.display_name, e.quantity) for e in stacks) == [
            ("Dagger", 1), ("Dagger +1", 1)]
        assert storage.location_entries(s, room.id) == []
        # give to another character
        plus = next(e for e in stacks if e.bonus == 1)
        alice_held = _locs(s, "u2")[0]
        characters.give(s, "u1", role="held", entry_id=plus.id, target_location_id=alice_held.id)
        (got,) = storage.location_entries(s, alice_held.id)
        assert got.display_name == "Dagger +1"


def _ac(s, uid="u1"):
    char = characters.require_character(s, uid)
    return character_sheet(s, char, include_stash=True).effective["ac"]


def test_armor_counts_toward_ac_from_held_only(dbsession):
    with session_scope() as s:
        _world(s)
        base = _ac(s).total
        _buy(s, "Leather Armor", 1)
        _buy(s, "Shield", 1)
        _buy(s, "Chainmail", 3, dest=_locs(s)[1].id)  # stash: ignored
        ac = _ac(s)
        assert ac.total == base + 2
        assert sorted(x["effect"] for x in ac.sources) == ["Leather Armor +1 (carried)", "Shield +1 (carried)"]
        leather = next(
            e for e in storage.location_entries(s, _locs(s)[0].id) if "Leather" in e.display_name)
        characters.move_stack(s, leather, _locs(s)[1], 1)
        ac = _ac(s)
        assert ac.total == base + 1
        assert [x["effect"] for x in ac.sources] == ["Shield +1 (carried)"]


def test_armor_bonus_picks_best_body_and_best_shield(dbsession):
    with session_scope() as s:
        _world(s)
        for name, b in (("Leather Armor", 1), ("Chainmail", 2), ("Shield", 1), ("Dagger", 3)):
            _buy(s, name, b)
        pairs = characters.armor_bonus(storage.location_entries(s, _locs(s)[0].id))
        assert sorted(pairs) == [("Chainmail +2", 2), ("Shield +1", 1)]


def test_type_change_blocked_by_bonus_stack(dbsession):
    with session_scope() as s:
        _world(s)
        dagger = s.query(Item).filter_by(name="Dagger").one()
        assert catalog.type_change_error(s, dagger, ITEM_TYPE_COMMON) is None
        _buy(s, "Dagger", 1)
        err = catalog.type_change_error(s, dagger, ITEM_TYPE_COMMON)
        assert err and "magic-bonus" in err
        assert catalog.type_change_error(s, dagger, ITEM_TYPE_ARMOR) is None


def test_db_unique_key_includes_bonus(dbsession):
    with session_scope() as s:
        room = _world(s)
        dagger = s.query(Item).filter_by(name="Dagger").one()
        s.add(InventoryEntry(location_id=room.id, item_id=dagger.id, quantity=1, bonus=0))
        s.add(InventoryEntry(location_id=room.id, item_id=dagger.id, quantity=1, bonus=1))
        s.flush()
        s.add(InventoryEntry(location_id=room.id, item_id=dagger.id, quantity=1, bonus=1))
        with pytest.raises(IntegrityError):
            s.flush()
        s.rollback()


# ---------- web ----------


def _web_world(api):
    with session_scope() as s:
        _world(s)
    return auth(login(api, "u1")["token"])


def _item(api, h, name):
    return next(i for i in api.get("/api/items", headers=h).json() if i["name"] == name)


def test_api_buy_and_sheet(api):
    h = _web_world(api)
    sheet = api.get("/api/character", headers=h).json()
    dagger = _item(api, h, "Dagger")
    body = {"quantity": 1, "pay_from": "character",
            "destination_location_id": sheet["held"]["location_id"], "bonus": 1}
    r = api.post(f"/api/items/{dagger['id']}/buy", json=body, headers=h)
    assert r.status_code == 200, r.text
    sheet = api.get("/api/character", headers=h).json()
    stack = _stack(sheet, "held", "Dagger +1")
    assert stack["bonus"] == 1 and stack["item_type"] == "weapon"
    too_high = api.post(f"/api/items/{dagger['id']}/buy", json={**body, "bonus": 4}, headers=h)
    assert too_high.status_code == 422
    rope = _item(api, h, "Rope")
    bad = api.post(f"/api/items/{rope['id']}/buy", json={**body, "bonus": 1}, headers=h)
    assert bad.status_code == 400 and "Only weapons and armor" in bad.json()["detail"]


def test_api_carry_bonus_and_ac(api):
    h = _web_world(api)
    r = api.post("/api/character/items", json={"name": "Leather Armor", "bonus": 1}, headers=h)
    assert r.status_code == 200, r.text
    sheet = api.get("/api/character", headers=h).json()
    assert sheet["effective"]["ac"]["total"] == 12
    assert sheet["effective"]["ac"]["sources"] == [{"effect": "Leather Armor +1 (carried)", "change": "+1"}]
    neg = api.post("/api/character/items", json={"name": "Dagger", "bonus": -1}, headers=h)
    assert neg.status_code == 422
