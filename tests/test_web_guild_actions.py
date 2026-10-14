"""Web API: take from guild storage, borrow and return treasury items."""

from test_web_api import _make_bob, _seed, _stack, api, auth, login  # noqa: F401

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import (
    ITEM_TYPE_MAGICAL,
    InventoryEntry,
    Item,
    Location,
    TreasuryEntry,
)
from shadowdark_bot.services import characters


def _guild_world(api):
    with session_scope() as s:
        _seed(s)
        _make_bob(s)
        characters.save_identity(s, "u2", name="Alice")
        rope = s.query(Item).filter_by(name="Rope").one()
        ring = Item(name="Ring", gear_slots=1, item_type=ITEM_TYPE_MAGICAL)
        room = Location(name="Storeroom", kind="inventory", max_gear_slots=20)
        vault = Location(name="Treasury", kind="treasury", max_gear_slots=0)
        s.add_all([ring, room, vault])
        s.flush()
        stack = InventoryEntry(location_id=room.id, item_id=rope.id, quantity=4)
        te = TreasuryEntry(location_id=vault.id, item_id=ring.id)
        s.add_all([stack, te])
        s.flush()
        ids = {"room": room.id, "rope": stack.id, "ring": te.id}
    return ids, auth(login(api, "u1")["token"]), auth(login(api, "u2")["token"])


def test_take_from_guild_storage(api):
    ids, bob, _alice = _guild_world(api)
    url = f"/api/guild/locations/{ids['room']}/items/{ids['rope']}/take"
    r = api.post(url, json={"quantity": 3}, headers=bob)
    assert r.status_code == 200
    assert r.json()["locations"][0]["items"][0]["quantity"] == 1
    held = api.get("/api/character", headers=bob).json()
    assert _stack(held, "held", "Rope")["quantity"] == 3
    r = api.post(url, json={"to": "stash"}, headers=bob)  # the rest, into the stash
    assert r.status_code == 200 and r.json()["locations"][0]["items"] == []
    assert _stack(api.get("/api/character", headers=bob).json(), "stash", "Rope")["quantity"] == 2


def test_take_errors(api):
    ids, bob, _alice = _guild_world(api)
    url = f"/api/guild/locations/{ids['room']}/items/{ids['rope']}/take"
    over = api.post(url, json={"quantity": 9}, headers=bob)
    assert over.status_code == 400 and "only has 4" in over.json()["detail"]
    assert api.post(url, json={"quantity": 0}, headers=bob).status_code == 400
    assert api.post(url, json={"to": "x"}, headers=bob).status_code == 422
    assert api.post(url, json={}).status_code == 401
    # Bob's own held location is not a guild location
    sheet = api.get("/api/character", headers=bob).json()
    arrow = _stack(sheet, "held", "Arrow")["id"]
    bad = api.post(
        f"/api/guild/locations/{sheet['held']['location_id']}/items/{arrow}/take",
        json={}, headers=bob)
    assert bad.status_code == 400 and "guild storage" in bad.json()["detail"]
    assert api.get("/api/guild", headers=bob).json()["locations"][0]["items"][0]["quantity"] == 4
    # a viewer without a character gets 404
    with session_scope() as s:
        characters.delete_character(s, "u2")
    nochar = auth(login(api, "u2")["token"])
    assert api.post(url, json={}, headers=nochar).status_code == 404


def test_take_capacity_error(api):
    ids, bob, _alice = _guild_world(api)
    with session_scope() as s:
        s.get(InventoryEntry, ids["rope"]).quantity = 15
    url = f"/api/guild/locations/{ids['room']}/items/{ids['rope']}/take"
    r = api.post(url, json={}, headers=bob)
    assert r.status_code == 400 and "slots" in r.json()["detail"]
    assert api.get("/api/guild", headers=bob).json()["locations"][0]["items"][0]["quantity"] == 15


def test_borrow_and_return_treasury_item(api):
    ids, bob, alice = _guild_world(api)
    r = api.post(f"/api/guild/treasury/{ids['ring']}/borrow", headers=bob)
    assert r.status_code == 200
    t = r.json()["treasury"][0]
    assert t["status"] == "borrowed" and t["borrower"]["user_id"] == "u1"
    assert _stack(api.get("/api/character", headers=bob).json(), "held", "Ring")["quantity"] == 1

    again = api.post(f"/api/guild/treasury/{ids['ring']}/borrow", headers=alice)
    assert again.status_code == 400 and "already borrowed" in again.json()["detail"]
    notmine = api.post(f"/api/guild/treasury/{ids['ring']}/return", headers=alice)
    assert notmine.status_code == 400 and "Only the person" in notmine.json()["detail"]

    r = api.post(f"/api/guild/treasury/{ids['ring']}/return", headers=bob)
    assert r.status_code == 200 and r.json()["treasury"][0]["status"] == "available"
    held = api.get("/api/character", headers=bob).json()["held"]["items"]
    assert all(i["name"] != "Ring" for i in held)
    assert api.post(f"/api/guild/treasury/{ids['ring']}/return", headers=bob).status_code == 400
    assert api.post("/api/guild/treasury/999/borrow", headers=bob).status_code == 400
    assert api.post(f"/api/guild/treasury/{ids['ring']}/borrow").status_code == 401
