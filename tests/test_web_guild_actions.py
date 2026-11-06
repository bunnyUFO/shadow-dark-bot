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

    # anyone can return it, not just the borrower
    r = api.post(f"/api/guild/treasury/{ids['ring']}/return", headers=alice)
    assert r.status_code == 200 and r.json()["treasury"][0]["status"] == "available"
    held = api.get("/api/character", headers=bob).json()["held"]["items"]
    assert all(i["name"] != "Ring" for i in held)
    assert api.post(f"/api/guild/treasury/{ids['ring']}/return", headers=bob).status_code == 400
    assert api.post("/api/guild/treasury/999/borrow", headers=bob).status_code == 400
    assert api.post(f"/api/guild/treasury/{ids['ring']}/borrow").status_code == 401


# ---------- add a location / store items ----------


def test_add_location_listed_for_any_viewer(api):
    ids, bob, _alice = _guild_world(api)
    r = api.post("/api/guild/locations",
                 json={"name": "Cellar", "max_gear_slots": 10, "description": "Damp"}, headers=bob)
    assert r.status_code == 200
    cellar = next(l for l in r.json()["locations"] if l["name"] == "Cellar")
    assert cellar["max_slots"] == 10 and cellar["description"] == "Damp"
    same = api.post("/api/guild/locations", json={"name": "Cellar", "max_gear_slots": 1}, headers=bob)
    assert same.status_code == 400 and "already exists" in same.json()["detail"]
    assert api.post("/api/guild/locations", json={"name": "X"}).status_code == 401
    # a viewer with no character can still add one
    with session_scope() as s:
        characters.delete_character(s, "u2")
    nochar = auth(login(api, "u2")["token"])
    r = api.post("/api/guild/locations", json={"name": "Attic", "max_gear_slots": 4}, headers=nochar)
    assert r.status_code == 200 and any(l["name"] == "Attic" for l in r.json()["locations"])


def test_store_two_items(api):
    ids, bob, _alice = _guild_world(api)
    sheet = api.get("/api/character", headers=bob).json()
    arrow, rope = _stack(sheet, "held", "Arrow"), _stack(sheet, "stash", "Rope")
    r = api.post(f"/api/guild/locations/{ids['room']}/store", headers=bob, json={"items": [
        {"role": "held", "entry_id": arrow["id"], "quantity": 5},
        {"role": "stash", "entry_id": rope["id"]},
    ]})
    assert r.status_code == 200
    room = next(l for l in r.json()["locations"] if l["location_id"] == ids["room"])
    names = {i["name"]: i["quantity"] for i in room["items"]}
    assert names == {"Rope": 5, "Arrow": 5}  # 4 ropes were already there
    after = api.get("/api/character", headers=bob).json()
    assert _stack(after, "held", "Arrow")["quantity"] == 20
    assert not any(i["name"] == "Rope" for i in after["stash"]["items"])


def test_store_rolls_back_when_a_later_item_does_not_fit(api):
    ids, bob, _alice = _guild_world(api)
    tight = api.post("/api/guild/locations", json={"name": "Tight", "max_gear_slots": 1}, headers=bob)
    tight_id = next(l for l in tight.json()["locations"] if l["name"] == "Tight")["location_id"]
    sheet = api.get("/api/character", headers=bob).json()
    arrow, rope = _stack(sheet, "held", "Arrow"), _stack(sheet, "stash", "Rope")
    guild_before = api.get("/api/guild", headers=bob).json()
    r = api.post(f"/api/guild/locations/{tight_id}/store", headers=bob, json={"items": [
        {"role": "stash", "entry_id": rope["id"]},  # fits (1 of 1)
        {"role": "held", "entry_id": arrow["id"], "quantity": 20},  # does not
    ]})
    assert r.status_code == 400 and "holds" in r.json()["detail"]
    assert api.get("/api/character", headers=bob).json() == sheet
    assert api.get("/api/guild", headers=bob).json() == guild_before


def test_store_errors(api):
    ids, bob, _alice = _guild_world(api)
    url = f"/api/guild/locations/{ids['room']}/store"
    sheet = api.get("/api/character", headers=bob).json()
    arrow = _stack(sheet, "held", "Arrow")["id"]
    assert api.post(url, json={"items": [{"role": "x", "entry_id": arrow}]}, headers=bob).status_code == 422
    assert api.post(url, json={"items": []}, headers=bob).status_code == 400
    assert api.post(url, json={"items": [{"role": "held", "entry_id": arrow}]}).status_code == 401
    idol = _stack(sheet, "held", "Idol")["id"]
    bad = api.post(url, json={"items": [{"role": "held", "entry_id": idol}]}, headers=bob)
    assert bad.status_code == 400
    with session_scope() as s:
        characters.delete_character(s, "u2")
    nochar = auth(login(api, "u2")["token"])
    ok_body = {"items": [{"role": "held", "entry_id": arrow}]}
    assert api.post(url, json=ok_body, headers=nochar).status_code == 404
