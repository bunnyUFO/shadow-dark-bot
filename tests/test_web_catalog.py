"""Web API: add and edit catalog items, and the description cleaner."""

from test_web_api import _make_bob, _seed, api, auth, login  # noqa: F401

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import (
    ITEM_TYPE_MAGICAL,
    InventoryEntry,
    Item,
    Location,
    TreasuryEntry,
)
from shadowdark_bot.services.catalog import clean_description


def _bob(api):
    with session_scope() as s:
        _seed(s)
        _make_bob(s)
    return auth(login(api, "u1")["token"])


def _rope_id():
    with session_scope() as s:
        return s.query(Item).filter_by(name="Rope").one().id


def _get(api, headers, name):
    return next(i for i in api.get("/api/items?limit=1000", headers=headers).json() if i["name"] == name)


def test_add_item_is_visible_in_the_catalog(api):
    bob = _bob(api)  # a player, not the DM
    r = api.post("/api/items", headers=bob, json={
        "name": "  Silk rope ", "item_type": "common", "gear_slots": 0.5, "bundle_size": 2,
        "value": "5gp 2sp", "description": "Quiet."})
    assert r.status_code == 200
    item = _get(api, bob, "Silk rope")
    assert item == r.json()
    assert (item["gear_slots"], item["bundle_size"], item["value_cp"], item["description"]) == (0.5, 2, 520, "Quiet.")


def test_add_item_errors(api):
    bob = _bob(api)
    ok = {"name": "Lantern", "item_type": "common"}
    dupe = api.post("/api/items", json={**ok, "name": "rope"}, headers=bob)
    assert dupe.status_code == 400 and "already exists" in dupe.json()["detail"]
    assert api.post("/api/items", json={**ok, "item_type": "gizmo"}, headers=bob).status_code == 400
    assert api.post("/api/items", json={**ok, "value": "lots"}, headers=bob).status_code == 400
    assert api.post("/api/items", json=ok).status_code == 401


def test_edit_each_field_and_clear(api):
    bob = _bob(api)
    rid = _rope_id()
    url = f"/api/items/{rid}"
    assert api.patch(url, json={"name": "Hemp rope"}, headers=bob).json()["name"] == "Hemp rope"
    assert api.patch(url, json={"item_type": "loot"}, headers=bob).json()["item_type"] == "loot"
    assert api.patch(url, json={"gear_slots": 2}, headers=bob).json()["gear_slots"] == 2
    assert api.patch(url, json={"bundle_size": 4}, headers=bob).json()["bundle_size"] == 4
    assert api.patch(url, json={"value": "2gp"}, headers=bob).json()["value_cp"] == 200
    assert api.patch(url, json={"description": "New"}, headers=bob).json()["description"] == "New"
    # omitted fields stay as they were
    after = _get(api, bob, "Hemp rope")
    assert (after["item_type"], after["gear_slots"], after["bundle_size"], after["value_cp"]) == ("loot", 2, 4, 200)
    cleared = api.patch(url, json={"description": "", "value": ""}, headers=bob).json()
    assert cleared["description"] is None and cleared["value_cp"] is None
    assert cleared["name"] == "Hemp rope"


def test_edit_name_rules(api):
    bob = _bob(api)
    rid = _rope_id()
    url = f"/api/items/{rid}"
    clash = api.patch(url, json={"name": "ARROW"}, headers=bob)
    assert clash.status_code == 400 and "already exists" in clash.json()["detail"]
    assert api.patch(url, json={"name": "ROPE"}, headers=bob).json()["name"] == "ROPE"  # case-only rename of itself
    assert api.patch(url, json={"name": "  "}, headers=bob).status_code == 400
    assert api.patch(url, json={"gear_slots": -1}, headers=bob).status_code == 400
    assert api.patch(url, json={"bundle_size": 0}, headers=bob).status_code == 400
    assert api.patch(url, json={"item_type": "gizmo"}, headers=bob).status_code == 400
    assert api.patch(url, json={"value": "lots"}, headers=bob).status_code == 400
    assert api.patch(url, json={"name": "X"}).status_code == 401


def test_edit_missing_item_is_a_400(api):
    bob = _bob(api)
    r = api.patch("/api/items/99999", json={"name": "Ghost"}, headers=bob)
    assert r.status_code == 400 and "no longer exists" in r.json()["detail"]


def test_type_change_blocked_by_stacks_and_treasury(api):
    bob = _bob(api)  # Bob carries Rope and Arrow
    blocked = api.patch(f"/api/items/{_rope_id()}", json={"item_type": "magical"}, headers=bob)
    assert blocked.status_code == 400 and "inventory stack" in blocked.json()["detail"]
    assert _get(api, bob, "Rope")["item_type"] == "common"
    with session_scope() as s:
        ring = Item(name="Ring", gear_slots=1, item_type=ITEM_TYPE_MAGICAL)
        vault = Location(name="Treasury", kind="treasury", max_gear_slots=0)
        s.add_all([ring, vault])
        s.flush()
        s.add(TreasuryEntry(location_id=vault.id, item_id=ring.id))
        ring_id = ring.id
    leave = api.patch(f"/api/items/{ring_id}", json={"item_type": "common"}, headers=bob)
    assert leave.status_code == 400 and "treasury" in leave.json()["detail"]
    # same type is not a change
    assert api.patch(f"/api/items/{ring_id}", json={"item_type": "magical", "gear_slots": 2},
                     headers=bob).status_code == 200


def test_clean_description():
    assert clean_description(None) is None and clean_description("") is None
    text = "Strong.\n**Gear slots:** 1\n**Type:** Common\n  **Value:** 1gp\n**Status:** ok\nSecond line."
    assert clean_description(text) == "Strong.\nSecond line."
    assert clean_description("**Gear slots:** 1\n**Type:** Common") is None
    assert clean_description("Has **bold** inline.") == "Has **bold** inline."


def test_api_returns_cleaned_descriptions(api):
    bob = _bob(api)
    with session_scope() as s:
        s.query(Item).filter_by(name="Rope").one().description = "50 feet\n**Gear slots:** 1\n**Value:** 1gp"
        s.query(Item).filter_by(name="Arrow").one().description = "**Type:** Common"
    assert _get(api, bob, "Rope")["description"] == "50 feet"
    assert _get(api, bob, "Arrow")["description"] is None
    sheet = api.get("/api/character", headers=bob).json()
    rope = next(i for i in sheet["stash"]["items"] if i["name"] == "Rope")
    assert rope["description"] == "50 feet"
