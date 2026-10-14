"""Treasury: anyone can return, borrow on someone's behalf, and borrowers have names."""

import pytest
from fastapi.testclient import TestClient
from test_web_api import BASE, SECRET, FakeOAuth, _stack, api, auth, login  # noqa: F401
from test_web_guild_actions import _guild_world

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import FREE_TEXT_BORROWER, Borrow, TreasuryEntry
from shadowdark_bot.services import characters
from shadowdark_bot.services import guild as guild_service
from shadowdark_bot.web.app import create_app
from shadowdark_bot.web.auth import SessionSigner

DISCORD_NAMES = {"u2": "AliceOnDiscord", "u3": "CarolOnDiscord"}


@pytest.fixture
def named_api(dbsession):
    """Like `api`, plus a Discord display-name resolver."""

    async def is_member(user_id):
        return True

    app = create_app(
        mode="discord",
        oauth=FakeOAuth(),
        signer=SessionSigner(SECRET, ttl_seconds=3600),
        is_allowed_member=is_member,
        public_base_url=BASE + "/",
        static_dir=None,
        resolve_name=DISCORD_NAMES.get,
    )
    return TestClient(app)


def _borrow_url(ids):
    return f"/api/guild/treasury/{ids['ring']}/borrow"


def _return_url(ids):
    return f"/api/guild/treasury/{ids['ring']}/return"


def test_return_by_non_borrower_and_by_viewer_without_character(api):
    ids, bob, _alice = _guild_world(api)
    api.post(_borrow_url(ids), headers=bob)
    with session_scope() as s:
        characters.delete_character(s, "u2")
    nochar = auth(login(api, "u2")["token"])
    r = api.post(_return_url(ids), headers=nochar)
    assert r.status_code == 200 and r.json()["treasury"][0]["status"] == "available"
    held = api.get("/api/character", headers=bob).json()["held"]["items"]
    assert all(i["name"] != "Ring" for i in held)  # gone from the borrower's sheet


def test_borrow_for_another_character(api):
    ids, bob, alice = _guild_world(api)
    r = api.post(_borrow_url(ids), json={"for_user_id": "u2"}, headers=bob)
    assert r.status_code == 200
    borrower = r.json()["treasury"][0]["borrower"]
    assert (borrower["user_id"], borrower["name"]) == ("u2", "Alice")
    assert [p["name"] for p in r.json()["party"]] == ["Alice", "Bob"]
    assert _stack(api.get("/api/character", headers=alice).json(), "held", "Ring")["quantity"] == 1
    bob_held = api.get("/api/character", headers=bob).json()["held"]["items"]
    assert all(i["name"] != "Ring" for i in bob_held)
    # returning clears it from Alice's sheet, whoever returns it
    api.post(_return_url(ids), headers=bob)
    alice_held = api.get("/api/character", headers=alice).json()["held"]["items"]
    assert all(i["name"] != "Ring" for i in alice_held)
    # an unknown character is refused and nothing is recorded
    bad = api.post(_borrow_url(ids), json={"for_user_id": "nobody"}, headers=bob)
    assert bad.status_code == 404
    assert api.get("/api/guild", headers=bob).json()["treasury"][0]["status"] == "available"


def test_viewer_without_character_can_borrow_for_others(api):
    ids, _bob, _alice = _guild_world(api)
    with session_scope() as s:
        characters.delete_character(s, "u2")
    nochar = auth(login(api, "u2")["token"])
    # for yourself still needs a sheet
    assert api.post(_borrow_url(ids), headers=nochar).status_code == 404
    r = api.post(_borrow_url(ids), json={"for_user_id": "u1"}, headers=nochar)
    assert r.status_code == 200 and r.json()["treasury"][0]["borrower"]["name"] == "Bob"
    assert api.post(_return_url(ids), headers=nochar).status_code == 200
    r = api.post(_borrow_url(ids), json={"name": "Old Tom"}, headers=nochar)
    assert r.status_code == 200 and r.json()["treasury"][0]["borrower"]["name"] == "Old Tom"


def test_borrow_for_free_text_name_is_ledger_only(api):
    ids, bob, _alice = _guild_world(api)
    r = api.post(_borrow_url(ids), json={"name": "  Old Tom "}, headers=bob)
    assert r.status_code == 200
    borrower = r.json()["treasury"][0]["borrower"]
    assert borrower["name"] == "Old Tom" and borrower["user_id"] == FREE_TEXT_BORROWER
    held = api.get("/api/character", headers=bob).json()["held"]["items"]
    assert all(i["name"] != "Ring" for i in held)  # no inventory anywhere
    again = api.post(_borrow_url(ids), headers=bob)
    assert again.status_code == 400 and "already borrowed" in again.json()["detail"]
    assert api.post(_return_url(ids), headers=bob).status_code == 200
    blank = api.post(_borrow_url(ids), json={"name": "   "}, headers=bob)
    assert blank.status_code == 400 and "name" in blank.json()["detail"]


def test_borrower_name_resolution_order(named_api):
    ids, bob, _alice = _guild_world(named_api)

    def shown():
        g = named_api.get("/api/guild", headers=bob).json()
        return g["treasury"][0]["borrower"]["name"]

    with session_scope() as s:
        s.get(TreasuryEntry, ids["ring"]).status = "borrowed"
        s.add(Borrow(treasury_entry_id=ids["ring"], borrower_id="u2", borrower_name="stored"))
    assert shown() == "Alice"  # character name beats the Discord name
    with session_scope() as s:
        s.query(Borrow).one().borrower_id = "u3"
    assert shown() == "CarolOnDiscord"  # no character: Discord display name
    with session_scope() as s:
        s.query(Borrow).one().borrower_id = "u4"
    assert shown() == "stored"  # not resolvable: the name stored on the loan
    with session_scope() as s:
        s.query(Borrow).one().borrower_name = None
    assert shown() == "Unknown player"


def test_borrower_name_survives_a_failing_resolver(dbsession):
    def boom(_uid):
        raise RuntimeError("cache gone")

    b = Borrow(treasury_entry_id=1, borrower_id="u9", borrower_name="Fallback")
    assert guild_service.borrower_name(b, None, boom) == "Fallback"
    assert guild_service.borrower_name(b, "Hero", boom) == "Hero"
    free = Borrow(treasury_entry_id=1, borrower_id=FREE_TEXT_BORROWER, borrower_name="Tom")
    assert guild_service.borrower_name(free, "ignored", DISCORD_NAMES.get) == "Tom"


def test_service_borrow_for_name_and_already_borrowed(dbsession):
    ids, _bob, _alice = None, None, None
    with session_scope() as s:
        from test_guild_services import _world

        ids = _world(s)
        guild_service.borrow_for_name(s, ids["ring"], "Old Tom")
        assert s.query(Borrow).one().borrower_name == "Old Tom"
        with pytest.raises(guild_service.AlreadyBorrowed) as err:
            guild_service.borrow(s, ids["ring"], "u1")
        assert err.value.borrower_id == FREE_TEXT_BORROWER
        assert err.value.borrower_name == "Old Tom"
        assert guild_service.borrower_mention(err.value.borrower_id, err.value.borrower_name) == (
            "**Old Tom**"
        )
        assert guild_service.borrower_mention("123") == "<@123>"
        guild_service.return_borrow(s, ids["ring"])
        assert s.get(TreasuryEntry, ids["ring"]).status == "available"
