"""Tests for services.guild — taking from guild storage and treasury borrow/return."""

import pytest

from shadowdark_bot import storage
from shadowdark_bot.db import session_scope
from shadowdark_bot.models import (
    ITEM_TYPE_COMMON,
    ITEM_TYPE_MAGICAL,
    Borrow,
    InventoryEntry,
    Item,
    Location,
    TreasuryEntry,
)
from shadowdark_bot.services import characters, guild
from shadowdark_bot.services.characters import CharacterError, CharacterNotFound


def _world(session):
    """Ysolde (u1) and Brannoc (u2); a storeroom with 5 torches and 3 ropes; one
    ring in the treasury."""
    torch = Item(name="Torch", gear_slots=1, bundle_size=1, item_type=ITEM_TYPE_COMMON)
    rope = Item(name="Rope", gear_slots=1, bundle_size=1, item_type=ITEM_TYPE_COMMON)
    ring = Item(name="Ring", gear_slots=1, bundle_size=1, item_type=ITEM_TYPE_MAGICAL)
    room = Location(name="Storeroom", kind="inventory", max_gear_slots=20)
    vault = Location(name="Treasury", kind="treasury", max_gear_slots=0)
    session.add_all([torch, rope, ring, room, vault])
    session.flush()
    t = InventoryEntry(location_id=room.id, item_id=torch.id, quantity=5)
    session.add(t)
    session.add(InventoryEntry(location_id=room.id, item_id=rope.id, quantity=3))
    te = TreasuryEntry(location_id=vault.id, item_id=ring.id)
    session.add(te)
    characters.save_identity(session, "u1", name="Ysolde")
    characters.save_identity(session, "u2", name="Brannoc")
    session.flush()
    return {"room": room.id, "torch": t.id, "ring": te.id}


def _held(session, user="u1"):
    held = storage.held_location(session, user)
    return {e.display_name: e.quantity for e in storage.location_entries(session, held.id)}


def test_take_partial_quantity_moves_and_merges(dbsession):
    with session_scope() as s:
        ids = _world(s)
        res = guild.take(s, "u1", location_id=ids["room"], entry_id=ids["torch"], quantity=2)
        assert (res.display_name, res.quantity) == ("Torch", 2)
        guild.take(s, "u1", location_id=ids["room"], entry_id=ids["torch"], quantity=1)
        assert _held(s) == {"Torch": 3}
        assert s.get(InventoryEntry, ids["torch"]).quantity == 2


def test_take_whole_stack_into_stash_removes_source(dbsession):
    with session_scope() as s:
        ids = _world(s)
        guild.take(s, "u1", location_id=ids["room"], entry_id=ids["torch"], role="stash")
        assert s.get(InventoryEntry, ids["torch"]) is None
        stash = storage.stash_location(s, "u1")
        assert [e.quantity for e in storage.location_entries(s, stash.id)] == [5]


def test_take_errors(dbsession):
    with session_scope() as s:
        ids = _world(s)
        with pytest.raises(CharacterError, match="only has 5"):
            guild.take(s, "u1", location_id=ids["room"], entry_id=ids["torch"], quantity=6)
        with pytest.raises(CharacterError, match="≥ 1"):
            guild.take(s, "u1", location_id=ids["room"], entry_id=ids["torch"], quantity=0)
        # a character's held location is not guild storage
        held = storage.held_location(s, "u2")
        with pytest.raises(CharacterError, match="guild storage"):
            guild.take(s, "u1", location_id=held.id, entry_id=ids["torch"])
        with pytest.raises(CharacterError, match="no longer there"):
            guild.take(s, "u1", location_id=ids["room"], entry_id=99999)
        with pytest.raises(CharacterNotFound):
            guild.take(s, "nobody", location_id=ids["room"], entry_id=ids["torch"])


def test_take_respects_capacity_and_changes_nothing(dbsession):
    with session_scope() as s:
        ids = _world(s)
        held = storage.held_location(s, "u1")
        cap = int(held.max_gear_slots)
        s.get(InventoryEntry, ids["torch"]).quantity = cap + 1
        with pytest.raises(CharacterError, match="holds"):
            guild.take(s, "u1", location_id=ids["room"], entry_id=ids["torch"])
        assert s.get(InventoryEntry, ids["torch"]).quantity == cap + 1
        assert _held(s) == {}


def test_borrow_puts_item_in_held_and_return_removes_it(dbsession):
    with session_scope() as s:
        ids = _world(s)
        entry = guild.borrow(s, ids["ring"], "u1")
        assert entry.status == "borrowed"
        assert _held(s) == {"Ring": 1}
        assert guild.open_borrow(s, ids["ring"]).borrower_id == "u1"
        guild.return_borrow(s, ids["ring"], only_borrower="u1")
        assert s.get(TreasuryEntry, ids["ring"]).status == "available"
        assert _held(s) == {}
        assert guild.open_borrow(s, ids["ring"]) is None


def test_borrow_twice_fails_with_borrower(dbsession):
    with session_scope() as s:
        ids = _world(s)
        guild.borrow(s, ids["ring"], "u1")
        with pytest.raises(guild.AlreadyBorrowed) as err:
            guild.borrow(s, ids["ring"], "u2")
        assert err.value.borrower_id == "u1"
        with pytest.raises(CharacterError, match="No treasury entry"):
            guild.borrow(s, 999, "u2")


def test_borrow_without_a_character_is_ledger_only(dbsession):
    with session_scope() as s:
        ids = _world(s)
        guild.borrow(s, ids["ring"], "ghost")
        assert s.query(Borrow).count() == 1
        guild.return_borrow(s, ids["ring"])  # the bot lets anyone return


def test_return_rules(dbsession):
    with session_scope() as s:
        ids = _world(s)
        with pytest.raises(CharacterError, match="isn't currently borrowed"):
            guild.return_borrow(s, ids["ring"])
        guild.borrow(s, ids["ring"], "u1")
        with pytest.raises(CharacterError, match="Only the person"):
            guild.return_borrow(s, ids["ring"], only_borrower="u2")
        assert s.get(TreasuryEntry, ids["ring"]).status == "borrowed"


# ---------- create_location / store ----------


def _closet(session, slots=3):
    return guild.create_location(session, name="Closet", max_gear_slots=slots).id


def _store_world(session):
    """Ysolde carries 2 torches and 2 ropes, and has 2 more ropes in her stash."""
    ids = _world(session)
    characters.carry(session, "u1", item_name="Torch", quantity=2)
    characters.carry(session, "u1", item_name="Rope", quantity=2)
    characters.carry(session, "u1", item_name="Rope", quantity=2, role="stash")
    held = storage.held_location(session, "u1")
    stash = storage.stash_location(session, "u1")
    by_name = lambda loc: {e.display_name: e.id for e in storage.location_entries(session, loc.id)}
    ids.update(closet=_closet(session, 10), held=by_name(held), stash=by_name(stash))
    return ids


def _contents(session, location_id):
    return {e.display_name: e.quantity for e in storage.location_entries(session, location_id)}


def test_store_moves_stacks_from_held_and_stash(dbsession):
    with session_scope() as s:
        ids = _store_world(s)
        res = guild.store(s, "u1", location_id=ids["closet"], items=[
            ("held", ids["held"]["Torch"], None),
            ("stash", ids["stash"]["Rope"], None),
        ])
        assert res.location_name == "Closet" and len(res.moved) == 2
        assert _contents(s, ids["closet"]) == {"Torch": 2, "Rope": 2}
        assert _held(s) == {"Rope": 2}


def test_store_partial_quantity_leaves_the_rest(dbsession):
    with session_scope() as s:
        ids = _store_world(s)
        guild.store(s, "u1", location_id=ids["closet"], items=[("held", ids["held"]["Rope"], 1)])
        assert _contents(s, ids["closet"]) == {"Rope": 1}
        assert _held(s) == {"Torch": 2, "Rope": 1}


def test_store_is_all_or_nothing(dbsession):
    with session_scope() as s:
        ids = _store_world(s)
        ids["closet"] = guild.create_location(s, name="Tight", max_gear_slots=3).id
    with pytest.raises(CharacterError, match="slots free"):
        with session_scope() as s:
            guild.store(s, "u1", location_id=ids["closet"], items=[
                ("held", ids["held"]["Torch"], None),  # fits (2 of 3)
                ("held", ids["held"]["Rope"], None),  # does not
            ])
    with session_scope() as s:
        assert _contents(s, ids["closet"]) == {}
        assert _held(s) == {"Torch": 2, "Rope": 2}


def test_store_over_capacity_reports_the_whole_selection(dbsession):
    # QA case: capacity 4, 2 used, then 3 torches + 1 longsword (4 slots) selected.
    with session_scope() as s:
        _world(s)
        sword = Item(name="Longsword", gear_slots=1, bundle_size=1, item_type=ITEM_TYPE_COMMON)
        s.add(sword)
        s.flush()
        characters.carry(s, "u1", item_name="Torch", quantity=3)
        characters.carry(s, "u1", item_name="Longsword", quantity=1)
        held = {e.display_name: e.id for e in storage.location_entries(
            s, storage.held_location(s, "u1").id)}
        loc = guild.create_location(s, name="Chest", max_gear_slots=4)
        storage.add_stack(s, loc, quantity=2, item=s.query(Item).filter_by(name="Rope").one())
        loc_id = loc.id
    with pytest.raises(CharacterError) as err:
        with session_scope() as s:
            guild.store(s, "u1", location_id=loc_id, items=[
                ("held", held["Torch"], None), ("held", held["Longsword"], None)])
    assert str(err.value) == "**Chest** has 2 of 4 slots free; these items need 4."
    with session_scope() as s:
        assert _contents(s, loc_id) == {"Rope": 2}
        assert _held(s) == {"Torch": 3, "Longsword": 1}


def test_store_refuses_magical_and_freeform(dbsession):
    with session_scope() as s:
        ids = _store_world(s)
        ring = s.query(Item).filter_by(name="Ring").one()
        held = storage.held_location(s, "u1")
        magic = InventoryEntry(location_id=held.id, item_id=ring.id, quantity=1)
        s.add(magic)
        s.flush()
        characters.carry(s, "u1", item_name="Idol", quantity=1, freeform_slots=0.5)
        idol = next(e.id for e in storage.location_entries(s, held.id) if e.display_name == "Idol")
        for entry in (magic.id, idol):
            with pytest.raises(CharacterError):
                guild.store(s, "u1", location_id=ids["closet"], items=[("held", entry, None)])


def test_store_rejects_empty_list_and_non_guild_target(dbsession):
    with session_scope() as s:
        ids = _store_world(s)
        with pytest.raises(CharacterError, match="at least one"):
            guild.store(s, "u1", location_id=ids["closet"], items=[])
        stash = storage.stash_location(s, "u1")
        with pytest.raises(CharacterError, match="guild storage"):
            guild.store(s, "u1", location_id=stash.id, items=[("held", ids["held"]["Torch"], None)])
        with pytest.raises(CharacterError, match="guild storage"):
            guild.store(s, "u1", location_id=99999, items=[("held", ids["held"]["Torch"], None)])


def test_create_location_rules(dbsession):
    with session_scope() as s:
        loc = guild.create_location(s, name="  Cellar ", max_gear_slots=10, description="  damp ")
        assert (loc.name, loc.max_gear_slots, loc.description, loc.kind) == ("Cellar", 10, "damp", "inventory")
        assert guild.create_location(s, name="Attic").max_gear_slots == 0
        with pytest.raises(CharacterError, match="empty"):
            guild.create_location(s, name="  ")
        with pytest.raises(CharacterError, match="≥ 0"):
            guild.create_location(s, name="Pit", max_gear_slots=-1)
        with pytest.raises(CharacterError, match="already exists"):
            guild.create_location(s, name="Cellar")
