"""Worn armor (only worn armor counts toward AC) and purchasable catalog items."""

import sqlite3

import pytest
from alembic import command
from fastapi.testclient import TestClient
from test_effect_library import _is_dm, _local_client, seeded  # noqa: F401
from test_magic_bonus import _ac, _buy, _buy_raw, _locs, _wear, _world  # noqa: F401
from test_migrations import _insert, alembic_cfg  # noqa: F401
from test_web_api import _stack, api, auth, login  # noqa: F401

from shadowdark_bot import core_gear, storage
from shadowdark_bot.db import session_scope
from shadowdark_bot.dev_seed import ALDRIC_ID, BRANNOC_ID
from shadowdark_bot.models import ITEM_TYPE_COMMON, Item, Location
from shadowdark_bot.services import catalog, characters, guild
from shadowdark_bot.services.characters import CharacterError


def _entry(s, name, uid="u1", role=0):
    loc = _locs(s, uid)[role]
    return next(e for e in storage.location_entries(s, loc.id) if e.display_name == name)


# ---------- wearing ----------


def test_wear_and_remove_changes_ac(dbsession):
    with session_scope() as s:
        _world(s)
        base = _ac(s).total
        _buy(s, "Leather Armor", 1)
        assert _ac(s).sources == []
        res = _wear(s, "Leather Armor +1")
        assert res.worn and res.took_off is None
        ac = _ac(s)
        assert ac.total == base + 1
        assert [x["effect"] for x in ac.sources] == ["Leather Armor +1 (worn)"]
        entry = _entry(s, "Leather Armor +1")
        characters.set_worn(s, "u1", entry_id=entry.id, worn=False)
        assert _ac(s).total == base and _ac(s).sources == []
        # taking off something not worn is a no-op
        characters.set_worn(s, "u1", entry_id=entry.id, worn=False)


def test_wearing_swaps_within_a_class_only(dbsession):
    with session_scope() as s:
        _world(s)
        base = _ac(s).total
        for name, b in (("Leather Armor", 1), ("Chainmail", 2), ("Shield", 1)):
            _buy(s, name, b)
        _wear(s, "Leather Armor +1")
        res = _wear(s, "Chainmail +2")
        assert res.took_off == "Leather Armor +1"
        assert not _entry(s, "Leather Armor +1").worn
        assert _ac(s).total == base + 2
        _wear(s, "Shield +1")
        assert _entry(s, "Chainmail +2").worn and _entry(s, "Shield +1").worn
        assert _ac(s).total == base + 3


def test_wear_refusals_change_nothing(dbsession):
    with session_scope() as s:
        _world(s)
        _buy(s, "Dagger", 0)
        _buy(s, "Leather Armor", 1, dest=_locs(s)[1].id)  # stash
        characters.carry(s, "u1", item_name="Idol", quantity=1)
        for name in ("Dagger", "Idol"):
            with pytest.raises(CharacterError, match="Only armor and shields can be worn."):
                characters.set_worn(s, "u1", entry_id=_entry(s, name).id, worn=True)
        stash_entry = _entry(s, "Leather Armor +1", role=1)
        with pytest.raises(CharacterError, match="Move it to Carrying before wearing it."):
            characters.set_worn(s, "u1", entry_id=stash_entry.id, worn=True)
        assert not any(
            e.worn for r in (0, 1) for e in storage.location_entries(s, _locs(s)[r].id)
        )


def test_worn_resets_when_the_stack_leaves(dbsession):
    with session_scope() as s:
        room = _world(s)
        base = _ac(s).total
        _buy(s, "Leather Armor", 1)
        _buy(s, "Shield", 1)
        _buy(s, "Chainmail", 2)
        for name in ("Leather Armor +1", "Shield +1"):
            _wear(s, name)
        # give to another character
        characters.give(
            s, "u1", role="held", entry_id=_entry(s, "Leather Armor +1").id,
            target_location_id=_locs(s, "u2")[0].id,
        )
        assert not _entry(s, "Leather Armor +1", uid="u2").worn
        # store in guild storage and take back
        guild.store(s, "u1", location_id=room.id,
                    items=[("held", _entry(s, "Shield +1").id, None)])
        (stored,) = storage.location_entries(s, room.id)
        assert not stored.worn
        guild.take(s, "u1", location_id=room.id, entry_id=stored.id)
        assert not _entry(s, "Shield +1").worn
        assert _ac(s).total == base
        # carrying to the stash and back
        _wear(s, "Chainmail +2")
        characters.move_stack(s, _entry(s, "Chainmail +2"), _locs(s)[1], 1)
        assert not _entry(s, "Chainmail +2", role=1).worn
        assert _ac(s).total == base


def test_partial_move_keeps_the_source_worn(dbsession):
    with session_scope() as s:
        _world(s)
        _buy(s, "Shield", 1, qty=2)
        _wear(s, "Shield +1")
        characters.move_stack(s, _entry(s, "Shield +1"), _locs(s)[1], 1)
        assert _entry(s, "Shield +1").worn and _entry(s, "Shield +1").quantity == 1
        assert not _entry(s, "Shield +1", role=1).worn


def test_changing_type_away_from_armor_clears_worn(dbsession):
    with session_scope() as s:
        _world(s)
        _buy(s, "Leather Armor", 1)
        _wear(s, "Leather Armor +1")
        item = s.query(Item).filter_by(name="Leather Armor").one()
        # a bonus stack blocks leaving armor, so use a +0 stack for this check
        _entry(s, "Leather Armor +1").bonus = 0
        s.flush()
        catalog.update_item(s, item.id, item_type=ITEM_TYPE_COMMON)
        assert not _entry(s, "Leather Armor").worn


# ---------- buying ----------


def test_buy_refusals(dbsession):
    with session_scope() as s:
        _world(s)
        s.add_all([
            Item(name="Mystery", gear_slots=1, item_type=ITEM_TYPE_COMMON, value_cp=10),
        ])
        s.flush()
        _buy(s, "Rope", 0)  # a purchasable item still works
        gold = characters.require_character(s, "u1").gold_cp
        with pytest.raises(CharacterError, match=r"\*\*Mystery\*\* isn't for sale"):
            _buy_raw(s, "Mystery", 0)
        with pytest.raises(CharacterError, match="can't be bought"):
            _buy_raw(s, "Dagger", 1)
        # the bonus check comes before the for-sale check
        with pytest.raises(CharacterError, match="can't be bought"):
            _buy_raw(s, "Mystery", 1)
        assert characters.require_character(s, "u1").gold_cp == gold
        assert [e.display_name for e in storage.location_entries(s, _locs(s)[0].id)] == ["Rope"]


# ---------- API ----------


def test_wear_endpoint_dm_and_player(seeded):  # noqa: F811
    dm = _local_client(_is_dm)  # signs in as Brannoc, a Dungeon Master here
    player = _local_client()
    with session_scope() as s:
        leather = _entry(s, "Leather Armor", uid=ALDRIC_ID)
        entry_id = leather.id
        assert leather.worn
    url = f"/api/characters/{ALDRIC_ID}/items/{entry_id}/wear"
    assert player.post(url, json={"worn": False}).status_code == 403
    r = dm.post(url, json={"worn": False})
    assert r.status_code == 200, r.text
    assert _stack(r.json(), "held", "Leather Armor")["worn"] is False
    r = dm.post(url, json={"worn": True})
    assert _stack(r.json(), "held", "Leather Armor")["worn"] is True
    bad = dm.post(f"/api/characters/{ALDRIC_ID}/items/{entry_id}/wear", json={})
    assert bad.status_code == 422


def test_buy_endpoint_refusals(api):
    with session_scope() as s:
        _world(s)
        s.add(Item(name="Mystery", gear_slots=1, item_type=ITEM_TYPE_COMMON, value_cp=10))
    h = auth(login(api, "u1")["token"])
    sheet = api.get("/api/character", headers=h).json()
    held = sheet["held"]["location_id"]
    items = {i["name"]: i for i in api.get("/api/items", headers=h).json()}
    assert items["Rope"]["purchasable"] is True and items["Mystery"]["purchasable"] is False
    body = {"quantity": 1, "pay_from": "character", "destination_location_id": held}

    def buy(name, **extra):
        return api.post(f"/api/items/{items[name]['id']}/buy", json={**body, **extra}, headers=h)

    assert buy("Rope").status_code == 200
    no = buy("Mystery")
    assert no.status_code == 400 and "isn't for sale" in no.json()["detail"]
    plus = buy("Dagger", bonus=1)
    assert plus.status_code == 400 and "can't be bought" in plus.json()["detail"]
    assert buy("Dagger", bonus=4).status_code == 422
    after = api.get("/api/character", headers=h).json()
    assert after["gold_cp"] == sheet["gold_cp"] - 100
    assert [i["name"] for i in after["held"]["items"]] == ["Rope"]


def test_catalog_api_purchasable(api):
    with session_scope() as s:
        _world(s)
    h = auth(login(api, "u1")["token"])
    plain = api.post("/api/items", headers=h, json={"name": "Lamp", "item_type": "common"})
    assert plain.json()["purchasable"] is False
    yes = api.post(
        "/api/items", headers=h,
        json={"name": "Candle", "item_type": "common", "purchasable": True},
    )
    assert yes.json()["purchasable"] is True
    url = f"/api/items/{yes.json()['id']}"
    assert api.patch(url, headers=h, json={"purchasable": False}).json()["purchasable"] is False
    assert api.patch(url, headers=h, json={"name": "Candles"}).json()["purchasable"] is False
    assert api.patch(url, headers=h, json={"purchasable": True}).json()["purchasable"] is True
    listed = {i["name"]: i for i in api.get("/api/items", headers=h).json()}
    assert listed["Candles"]["purchasable"] is True and listed["Lamp"]["purchasable"] is False


# ---------- core gear and the dev seed ----------


def test_core_gear_matching():
    assert core_gear.is_core_gear("Flask of Oil", "common")
    assert core_gear.is_core_gear("Arrows (20)", "common")
    assert core_gear.is_core_gear("Rope, 60'", "common")
    assert core_gear.is_core_gear("Shield", "armor")
    assert not core_gear.is_core_gear("Arrows", "potion")
    assert not core_gear.is_core_gear("Longsword +1", "weapon")
    assert not core_gear.is_core_gear("Torch", "magical")
    assert not core_gear.is_core_gear("Potion of Healing", "potion")


def test_dev_seed_wears_and_sells(seeded):  # noqa: F811
    with session_scope() as s:
        char = characters.require_character(s, BRANNOC_ID)
        from shadowdark_bot.web.schemas import character_sheet

        ac = character_sheet(s, char, include_stash=True).effective["ac"]
        assert "Shield +1 (worn)" in [x["effect"] for x in ac.sources]
        by_name = {i.name: i for i in s.query(Item).all()}
        assert by_name["Potion of Healing"].purchasable is False
        assert by_name["Dagger"].purchasable is True


# ---------- migration ----------


def test_migration_backfills_worn_and_purchasable(alembic_cfg):  # noqa: F811
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "0024_stack_bonus")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    _insert(conn, "player_characters", user_id="u1", name="Bob")
    items = {
        name: _insert(conn, "items", name=name, item_type=kind)
        for name, kind in (
            ("Leather Armor", "armor"), ("Chainmail", "armor"), ("shield", "armor"),
            ("Rope, 60'", "common"), ("Torch", "magical"), ("Dagger +1", "weapon"),
            ("Potion of Healing", "potion"),
        )
    }
    held = _insert(conn, "locations", name="Held", kind="inventory", owner_user_id="u1",
                   role="held", max_gear_slots=10)
    stash = _insert(conn, "locations", name="Stash", kind="inventory", owner_user_id="u1",
                    role="stash", max_gear_slots=10)
    _insert(conn, "inventory_entries", location_id=held, item_id=items["Leather Armor"],
            quantity=1, bonus=1)
    chain = _insert(conn, "inventory_entries", location_id=held, item_id=items["Chainmail"],
                    quantity=1, bonus=2)
    _insert(conn, "inventory_entries", location_id=held, item_id=items["shield"],
            quantity=1, bonus=0)
    _insert(conn, "inventory_entries", location_id=stash, item_id=items["shield"],
            quantity=1, bonus=3)
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")

    conn = sqlite3.connect(db_path)
    try:
        sellable = {n for (n,) in conn.execute("SELECT name FROM items WHERE purchasable = 1")}
        assert sellable == {"Leather Armor", "Chainmail", "shield", "Rope, 60'"}
        worn = [r[0] for r in conn.execute("SELECT id FROM inventory_entries WHERE worn = 1")]
        assert worn == [chain]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()

    command.downgrade(cfg, "0024_stack_bonus")
    conn = sqlite3.connect(db_path)
    try:
        assert "worn" not in [r[1] for r in conn.execute("PRAGMA table_info(inventory_entries)")]
        assert "purchasable" not in [r[1] for r in conn.execute("PRAGMA table_info(items)")]
    finally:
        conn.close()
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    try:
        assert conn.execute("SELECT count(*) FROM items WHERE purchasable = 1").fetchone()[0] == 4
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def test_migration_tie_goes_to_lowest_id(alembic_cfg):  # noqa: F811
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, "0024_stack_bonus")
    conn = sqlite3.connect(db_path)
    _insert(conn, "player_characters", user_id="u1", name="Bob")
    a = _insert(conn, "items", name="Leather Armor", item_type="armor")
    b = _insert(conn, "items", name="Chainmail", item_type="armor")
    held = _insert(conn, "locations", name="Held", kind="inventory", owner_user_id="u1",
                   role="held", max_gear_slots=10)
    first = _insert(conn, "inventory_entries", location_id=held, item_id=a, quantity=1, bonus=1)
    _insert(conn, "inventory_entries", location_id=held, item_id=b, quantity=1, bonus=1)
    conn.commit()
    conn.close()
    command.upgrade(cfg, "head")
    conn = sqlite3.connect(db_path)
    try:
        assert [r[0] for r in conn.execute("SELECT id FROM inventory_entries WHERE worn=1")] == [first]
    finally:
        conn.close()
