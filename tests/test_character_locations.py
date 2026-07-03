"""Tests for the location-backed character inventory: storage helpers, give
validation/targeting, and the carry/give/remove flows driven through a fake
Discord interaction."""

import asyncio

from shadowdark_bot import storage
from shadowdark_bot.cogs import magical_treasury as mt
from shadowdark_bot.cogs import player_characters as pc
from shadowdark_bot.models import (
    ITEM_TYPE_COMMON,
    ITEM_TYPE_MAGICAL,
    Item,
    Location,
    PlayerCharacter,
    TreasuryEntry,
)

# ---------- fakes ----------


class _Resp:
    def __init__(self):
        self.messages = []
        self._done = False

    def is_done(self):
        return self._done

    async def send_message(self, content=None, **kw):
        self.messages.append(content)
        self._done = True

    async def edit_message(self, content=None, **kw):
        self._done = True

    async def defer(self, *a, **k):
        self._done = True

    async def send_modal(self, modal):
        self._done = True


class _Followup:
    def __init__(self):
        self.sent = []

    async def send(self, content=None, **kw):
        self.sent.append(content)


class _User:
    def __init__(self, uid):
        self.id = uid
        self.mention = f"<@{uid}>"
        self.display_name = f"U{uid}"


class FakeInteraction:
    def __init__(self, uid="u1"):
        self.response = _Resp()
        self.followup = _Followup()
        self.user = _User(uid)

    @property
    def error(self):
        """First error message sent (starts with **Failed), or None."""
        for m in self.response.messages:
            if m and m.startswith("**Failed"):
                return m
        return None

    @property
    def confirmation(self):
        return self.followup.sent[-1] if self.followup.sent else None


def run(coro):
    return asyncio.run(coro)


def _make_char(session, user_id, name, str_score):
    char = PlayerCharacter(user_id=user_id, name=name, str_score=str_score)
    session.add(char)
    session.flush()
    storage.ensure_character_locations(session, char)
    return char


# ---------- storage ----------


def test_ensure_creates_held_and_stash_with_caps(dbsession):
    with dbsession() as s:
        _make_char(s, "u1", "Bob", 15)
        s.commit()
    with dbsession() as s:
        held = storage.held_location(s, "u1")
        stash = storage.stash_location(s, "u1")
        assert held.name == "Bob"
        assert held.max_gear_slots == 15  # max(10, STR)
        assert stash.name == "Bob Guild Stash"
        assert stash.max_gear_slots == 10


def test_ensure_syncs_name_and_cap(dbsession):
    with dbsession() as s:
        char = _make_char(s, "u1", "Bob", 8)
        assert storage.held_location(s, "u1").max_gear_slots == 10  # floor
        char.name = "Bobby"
        char.str_score = 16
        storage.ensure_character_locations(s, char)
        s.commit()
    with dbsession() as s:
        held = storage.held_location(s, "u1")
        assert held.name == "Bobby"
        assert held.max_gear_slots == 16
        assert storage.stash_location(s, "u1").name == "Bobby Guild Stash"


def test_add_stack_merges_catalog_and_dedupes_freeform(dbsession):
    with dbsession() as s:
        _make_char(s, "u1", "Bob", 15)
        rope = Item(name="Rope", gear_slots=1, bundle_size=1, item_type=ITEM_TYPE_COMMON)
        s.add(rope)
        s.flush()
        held = storage.held_location(s, "u1")
        storage.add_stack(s, held, quantity=1, item=rope)
        storage.add_stack(s, held, quantity=2, item=rope)
        storage.add_stack(s, held, quantity=1, freeform_name="Gem", slots_each=0.5)
        storage.add_stack(s, held, quantity=1, freeform_name="gem", slots_each=0.5)
        s.flush()
        entries = storage.location_entries(s, held.id)
        by_name = {e.display_name: e.quantity for e in entries}
        assert by_name["Rope"] == 3  # merged catalog
        assert by_name["Gem"] == 2  # freeform de-duped by name (case-insensitive)


# ---------- give validation / targeting ----------


def test_give_targets_exclude_other_stash_and_source(dbsession):
    with dbsession() as s:
        _make_char(s, "u1", "Bob", 15)
        _make_char(s, "u2", "Amy", 12)
        s.add(Location(name="Armory", kind="inventory", max_gear_slots=50))
        s.commit()
    with dbsession() as s:
        held = storage.held_location(s, "u1")
        labels = [lbl for _, lbl in pc._give_targets(s, "u1", held)]
        assert any("Armory" in lbl for lbl in labels)  # guild location
        assert any("Your stash" in lbl for lbl in labels)  # own stash
        assert any("Amy" in lbl for lbl in labels)  # other char held
        # Amy's stash must NOT be offered.
        assert not any("Amy Guild Stash" in lbl for lbl in labels)
        # Source (own held) is excluded.
        assert not any("Your held" in lbl for lbl in labels)


def test_validate_give_blocks_magical_and_freeform_into_guild(dbsession):
    with dbsession() as s:
        _make_char(s, "u1", "Bob", 15)
        guild = Location(name="Armory", kind="inventory", max_gear_slots=50)
        amulet = Item(name="Amulet", gear_slots=1, item_type=ITEM_TYPE_MAGICAL)
        s.add_all([guild, amulet])
        s.flush()
        held = storage.held_location(s, "u1")
        mag = storage.add_stack(s, held, quantity=1, item=amulet)
        free = storage.add_stack(s, held, quantity=1, freeform_name="Idol", slots_each=1)
        s.flush()
        assert "Magical" in pc._validate_give_target("u1", held, guild, mag)
        assert "Freeform" in pc._validate_give_target("u1", held, guild, free)
        # freeform into another character's held is allowed
        _make_char(s, "u2", "Amy", 12)
        amy_held = storage.held_location(s, "u2")
        assert pc._validate_give_target("u1", held, amy_held, free) is None


# ---------- flows through _do_* ----------


def _seed_common(session):
    rope = Item(name="Rope", gear_slots=1, bundle_size=1, item_type=ITEM_TYPE_COMMON)
    arrow = Item(name="Arrow", gear_slots=1, bundle_size=20, item_type=ITEM_TYPE_COMMON)
    session.add_all([rope, arrow])
    _make_char(session, "u1", "Bob", 15)
    _make_char(session, "u2", "Amy", 12)
    session.flush()


def test_carry_enforces_held_capacity(dbsession):
    with dbsession() as s:
        _seed_common(s)
        s.commit()
    # 400 arrows = 20 slots > held cap 15 -> fail
    i = FakeInteraction("u1")
    run(pc._do_carry(i, user_id="u1", item_name="Arrow", quantity=400))
    assert i.error is not None and "20 more" in i.error


def test_give_moves_between_characters(dbsession):
    with dbsession() as s:
        _seed_common(s)
        s.commit()
    i = FakeInteraction("u1")
    run(pc._do_carry(i, user_id="u1", item_name="Rope", quantity=3))
    with dbsession() as s:
        held = storage.held_location(s, "u1")
        eid = storage.location_entries(s, held.id)[0].id
        amy_held_id = storage.held_location(s, "u2").id
    i = FakeInteraction("u1")
    run(pc._do_give(i, user_id="u1", role="held", entry_id=eid,
                    target_location_id=amy_held_id, quantity=2))
    assert i.error is None
    with dbsession() as s:
        bob_held = storage.held_location(s, "u1")
        amy_held = storage.held_location(s, "u2")
        assert storage.location_entries(s, bob_held.id)[0].quantity == 1
        assert storage.location_entries(s, amy_held.id)[0].quantity == 2


def test_borrow_and_remove_roundtrip_through_treasury(dbsession):
    with dbsession() as s:
        _make_char(s, "u1", "Bob", 15)
        amulet = Item(name="Amulet", gear_slots=1, item_type=ITEM_TYPE_MAGICAL)
        s.add(amulet)
        s.flush()
        loc = mt._get_or_create_treasury_location(s)
        te = TreasuryEntry(location_id=loc.id, item_id=amulet.id, status="available")
        s.add(te)
        s.commit()
        te_id = te.id
        amulet_id = amulet.id
    # borrow -> mirrored into held
    run(mt._do_borrow(FakeInteraction("u1"), te_id, _User("u1")))
    with dbsession() as s:
        held = storage.held_location(s, "u1")
        stack = storage.find_catalog_stack(s, held.id, amulet_id)
        assert stack is not None and stack.quantity == 1
        eid = stack.id
        assert s.get(TreasuryEntry, te_id).status == "borrowed"
    # remove from held -> auto-returns to treasury
    i = FakeInteraction("u1")
    run(pc._do_remove(i, user_id="u1", role="held", entry_id=eid))
    with dbsession() as s:
        assert s.get(TreasuryEntry, te_id).status == "available"
        held = storage.held_location(s, "u1")
        assert storage.find_catalog_stack(s, held.id, amulet_id) is None
