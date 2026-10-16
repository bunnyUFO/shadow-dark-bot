"""Dungeon Masters: DM_USER_IDS / DM_ROLE_IDS users may edit every sheet over
/api/characters/{user_id}/…; everyone else stays limited to their own."""

import asyncio

import pytest
from fastapi.testclient import TestClient
from test_web_api import BASE, LAN, SECRET, FakeOAuth, _make_bob, _seed, _stack, auth, login

from shadowdark_bot.db import session_scope
from shadowdark_bot.services import characters
from shadowdark_bot.web.app import DM_ONLY, create_app
from shadowdark_bot.web.auth import SessionSigner

ROLE_DM = "rdm"  # holds a DM role in Discord


@pytest.fixture
def dm_api(dbsession):
    """Members u1 (Bob), u2 (Amy), dm (listed in DM_USER_IDS, no character)
    and rdm (has a DM role)."""
    role_checks = []

    async def is_member(user_id):
        return user_id in {"u1", "u2", "dm", ROLE_DM}

    async def has_dm_role(user_id):
        role_checks.append(user_id)
        return user_id == ROLE_DM

    app = create_app(
        mode="discord",
        oauth=FakeOAuth(),
        signer=SessionSigner(SECRET, ttl_seconds=3600),
        is_allowed_member=is_member,
        public_base_url=BASE + "/",
        static_dir=None,
        dm_user_ids=frozenset({"dm"}),
        has_dm_role=has_dm_role,
    )
    client = TestClient(app)
    client.role_checks = role_checks
    with session_scope() as s:
        _seed(s)
        _make_bob(s)
        characters.save_identity(s, "u2", name="Amy", char_class="Thief")
    return client


def _as(client, uid):
    return auth(login(client, uid)["token"])


def test_players_are_not_dms(dm_api):
    amy = _as(dm_api, "u2")
    assert dm_api.get("/api/me", headers=amy).json()["is_dm"] is False
    assert dm_api.role_checks == ["u2"]


def test_player_cannot_edit_someone_else(dm_api):
    amy = _as(dm_api, "u2")
    denied = dm_api.patch("/api/characters/u1/gold", json={"gold": "1cp"}, headers=amy)
    assert denied.status_code == 403 and denied.json()["detail"] == DM_ONLY
    assert dm_api.post("/api/characters/u1/items", json={"name": "Rope"},
                       headers=amy).status_code == 403
    assert dm_api.post("/api/characters/u1/spells/rest", headers=amy).status_code == 403
    assert dm_api.post("/api/characters/u1/effects", json={"name": "Hex"},
                       headers=amy).status_code == 403
    sheet = dm_api.get("/api/characters/u1", headers=amy).json()
    assert sheet["gold_cp"] == 1050 and sheet["stash"] is None  # untouched, stash hidden


def test_player_may_use_their_own_id(dm_api):
    bob = _as(dm_api, "u1")
    r = dm_api.patch("/api/characters/u1/gold", json={"gold": "2gp"}, headers=bob)
    assert r.status_code == 200 and r.json()["gold_cp"] == 200


def test_dm_by_user_id_edits_any_sheet(dm_api):
    c, dm = dm_api, _as(dm_api, "dm")
    me = c.get("/api/me", headers=dm).json()
    assert me["is_dm"] is True and me["has_character"] is False
    base = "/api/characters/u1"

    sheet = c.get(base, headers=dm).json()
    assert _stack(sheet, "stash", "Rope")  # a DM sees the stash

    r = c.patch(f"{base}/gold", json={"gold": "7gp"}, headers=dm)
    assert r.status_code == 200 and r.json()["name"] == "Bob" and r.json()["gold_cp"] == 700
    assert r.json()["stash"] is not None  # writes return the full sheet

    r = c.post(f"{base}/items", json={"name": "Rope", "to": "stash"}, headers=dm)
    assert _stack(r.json(), "stash", "Rope")["quantity"] == 2
    arrow = _stack(r.json(), "held", "Arrow")["id"]
    r = c.delete(f"{base}/items/{arrow}?quantity=5", headers=dm)
    assert _stack(r.json(), "held", "Arrow")["quantity"] == 20
    r = c.patch(f"{base}/items/{arrow}", json={"quantity": 10, "notes": "fletched"}, headers=dm)
    assert _stack(r.json(), "held", "Arrow")["quantity"] == 10

    targets = c.get(f"{base}/items/{arrow}/give-targets", headers=dm).json()
    stash = next(t for t in targets if t["kind"] == "self")
    assert stash["owner_user_id"] == "u1"  # "self" is Bob, the sheet being edited
    assert any(t["kind"] == "character" and t["owner_user_id"] == "u2" for t in targets)
    r = c.post(f"{base}/items/{arrow}/give", json={"target_location_id": stash["location_id"]},
               headers=dm)
    assert _stack(r.json(), "stash", "Arrow")["quantity"] == 10

    spells = {s["name"]: s["id"] for s in c.get("/api/spells", headers=dm).json()}
    fireball = next(k for k in r.json()["spells"] if k["name"] == "Fireball")["id"]
    r = c.delete(f"{base}/spells/{fireball}", headers=dm)
    assert [k["name"] for k in r.json()["spells"]] == ["Light"]
    r = c.post(f"{base}/spells", json={"spell_id": spells["Fireball"]}, headers=dm)
    light = next(k for k in r.json()["spells"] if k["name"] == "Light")["id"]
    r = c.patch(f"{base}/spells/{light}", json={"lost": True}, headers=dm)
    assert next(k for k in r.json()["spells"] if k["id"] == light)["lost"] is True
    r = c.post(f"{base}/spells/rest", headers=dm)
    assert not any(k["lost"] for k in r.json()["spells"])

    r = c.post(f"{base}/effects", json={"name": "Bless", "max_rounds": 3}, headers=dm)
    effect = r.json()["effects"][0]["id"]
    r = c.patch(f"{base}/effects/{effect}", json={"rounds_used": 1}, headers=dm)
    assert r.json()["effects"][0]["rounds_left"] == 2
    r = c.delete(f"{base}/effects/{effect}", headers=dm)
    assert r.json()["effects"] == []

    r = c.patch(f"{base}/identity", json={"name": "Bob the Bold", "char_class": "Wizard"},
                headers=dm)
    assert r.json()["name"] == "Bob the Bold"
    r = c.patch(f"{base}/stats", headers=dm, json={
        "scores": [15, 14, 10, 16, 10, 10], "level": 3, "xp": 0, "max_hp": 12,
        "armor_class": 11, "spell_ability": "int", "spell_check_bonus": 1,
    })
    assert r.json()["level"] == 3 and r.json()["max_hp"] == 12
    r = c.post(f"{base}/xp", json={"add": 4}, headers=dm)
    assert r.json()["xp"] == 4
    r = c.patch(f"{base}/skills", json={"languages": "Common, Draconic"}, headers=dm)
    assert "Draconic" in r.json()["languages"]
    r = c.patch(f"{base}/proficiencies", json={"proficiencies": "Staff"}, headers=dm)
    assert r.json()["proficiencies"] == "Staff"

    # Bob sees every change on his own sheet
    mine = c.get("/api/character", headers=_as(c, "u1")).json()
    assert (mine["name"], mine["gold_cp"], mine["level"]) == ("Bob the Bold", 700, 3)
    assert _stack(mine, "stash", "Arrow")["quantity"] == 10


def test_dm_by_discord_role(dm_api):
    dm = _as(dm_api, ROLE_DM)
    assert dm_api.get("/api/me", headers=dm).json()["is_dm"] is True
    r = dm_api.patch("/api/characters/u2/gold", json={"gold": "3sp"}, headers=dm)
    assert r.status_code == 200 and r.json()["name"] == "Amy" and r.json()["gold_cp"] == 30


def test_dm_listed_by_id_skips_the_role_lookup(dm_api):
    _as(dm_api, "dm")
    assert dm_api.role_checks == []


def test_dm_cannot_edit_a_character_that_does_not_exist(dm_api):
    dm = _as(dm_api, "dm")
    assert dm_api.patch("/api/characters/nobody/gold", json={"gold": "1gp"},
                        headers=dm).status_code == 404
    assert dm_api.patch("/api/characters/nobody/identity", json={"name": "New"},
                        headers=dm).status_code == 404  # no creation over the web
    assert dm_api.get("/api/characters/nobody", headers=dm).status_code == 404


def test_only_a_signed_true_flag_makes_a_dm():
    signer = SessionSigner(SECRET, 60)
    assert signer.verify(signer.issue("u1", "Bob")).is_dm is False
    assert signer.verify(signer.issue("u1", "Bob", is_dm=True)).is_dm is True
    # tokens from before DMs existed, and odd values, are not DM tokens
    assert signer.verify(signer._serializer.dumps({"uid": "u1", "name": "Bob"})).is_dm is False
    assert signer.verify(signer._serializer.dumps({"uid": "u1", "dm": "yes"})).is_dm is False


def test_local_mode_dm(dbsession):
    from shadowdark_bot.web.server import build_local_app

    with session_scope() as s:
        _seed(s)
        _make_bob(s)
        characters.save_identity(s, "u2", name="Amy")
    c = TestClient(build_local_app(frozenset({"u2"})), client=LAN)
    amy = auth(c.post("/api/auth/local", json={"user_id": "u2"}).json()["token"])
    bob = auth(c.post("/api/auth/local", json={"user_id": "u1"}).json()["token"])
    assert c.get("/api/me", headers=amy).json()["is_dm"] is True
    assert c.get("/api/me", headers=bob).json()["is_dm"] is False
    r = c.patch("/api/characters/u1/gold", json={"gold": "1gp"}, headers=amy)
    assert r.status_code == 200 and r.json()["gold_cp"] == 100
    assert c.patch("/api/characters/u2/gold", json={"gold": "1gp"},
                   headers=bob).status_code == 403


# ---------- the bot's role lookup and settings ----------


class _Role:
    def __init__(self, role_id):
        self.id = role_id


class _Member:
    def __init__(self, *role_ids):
        self.roles = [_Role(r) for r in role_ids]


class _Guild:
    def __init__(self, members):
        self.members = members

    def get_member(self, uid):
        return None  # no members intent: always falls back to REST

    async def fetch_member(self, uid):
        import discord

        class _Resp:
            status = 404
            reason = "Not Found"

        if uid not in self.members:
            raise discord.NotFound(_Resp(), "Unknown Member")
        return self.members[uid]


class _Bot:
    def __init__(self, guilds):
        self.guilds = guilds

    async def wait_until_ready(self):
        return None

    def get_guild(self, gid):
        return self.guilds.get(gid)


def test_bot_dm_role_check():
    from shadowdark_bot.web.server import bot_dm_role_check

    allowed = _Guild({1: _Member(10, 77), 2: _Member(10), 3: _Member()})
    other = _Guild({4: _Member(77)})
    check = bot_dm_role_check(_Bot({100: allowed, 200: other}), {100}, {77})
    assert asyncio.run(check("1")) is True  # has the DM role
    assert asyncio.run(check("2")) is False  # other roles only
    assert asyncio.run(check("3")) is False
    assert asyncio.run(check("4")) is False  # DM role, but in a guild that isn't allowed
    assert asyncio.run(check("5")) is False  # not a member anywhere


def test_dm_settings():
    from shadowdark_bot.config import Settings

    s = Settings(DISCORD_TOKEN="t", DM_USER_IDS="111, 222,", DM_ROLE_IDS="10,20")
    assert s.dm_user_ids == frozenset({"111", "222"})
    assert s.dm_role_ids == {10, 20}
    assert Settings(DISCORD_TOKEN="t").dm_user_ids == frozenset()
    web = Settings(
        DISCORD_TOKEN="t", WEB_ENABLED=True, PUBLIC_BASE_URL=BASE, DISCORD_CLIENT_ID="1",
        DISCORD_CLIENT_SECRET="s", SESSION_SECRET=SECRET, ALLOWED_GUILD_IDS="1",
        DM_ROLE_IDS="10",
    )
    assert web.web_config_errors() == []
    bad = web.model_copy(update={"DM_ROLE_IDS": "Dungeon Master"})
    assert "DM_ROLE_IDS" in " ".join(bad.web_config_errors())
