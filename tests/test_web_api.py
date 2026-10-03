"""Tests for the web API: login exchange, session tokens, the guild allowlist,
and the read endpoints. Discord is faked; the database is the in-memory test DB."""

import httpx
import pytest
from fastapi.testclient import TestClient

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import ITEM_TYPE_COMMON, Item, Spell
from shadowdark_bot.services import characters
from shadowdark_bot.web.app import create_app
from shadowdark_bot.web.auth import (
    DiscordOAuth,
    DiscordUser,
    OAuthError,
    SessionSigner,
)

SECRET = "x" * 40
BASE = "https://shadowdark.example"


class FakeOAuth:
    """Stands in for DiscordOAuth: code 'good-<uid>' logs in as <uid>."""

    client_id = "app123"

    def __init__(self):
        self.redirect_uris = []

    async def exchange_code(self, code, redirect_uri):
        self.redirect_uris.append(redirect_uri)
        if not code.startswith("good-"):
            raise OAuthError("Discord rejected the login code.")
        return f"token-{code[5:]}"

    async def fetch_user(self, access_token):
        uid = access_token.removeprefix("token-")
        return DiscordUser(id=uid, username=f"user{uid}", global_name=f"User {uid}")


@pytest.fixture
def api(dbsession):
    members = {"u1", "u2"}
    oauth = FakeOAuth()

    async def is_member(user_id):
        return user_id in members

    app = create_app(
        oauth=oauth,
        signer=SessionSigner(SECRET, ttl_seconds=3600),
        is_allowed_member=is_member,
        public_base_url=BASE + "/",
    )
    client = TestClient(app)
    client.oauth = oauth
    return client


def login(client, uid="u1", flow="web"):
    resp = client.post("/api/auth/exchange", json={"code": f"good-{uid}", "flow": flow})
    assert resp.status_code == 200, resp.text
    return resp.json()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def _seed(session):
    session.add_all([
        Item(name="Rope", gear_slots=1, bundle_size=1, item_type=ITEM_TYPE_COMMON,
             description="50 feet", value_cp=100),
        Item(name="Arrow", gear_slots=1, bundle_size=20, item_type=ITEM_TYPE_COMMON),
        Spell(name="Light", tier=1, classes="priest,wizard", duration="1 hour"),
        Spell(name="Cure Wounds", tier=1, classes="priest"),
        Spell(name="Fireball", tier=3, classes="wizard"),
    ])
    session.flush()


def _make_bob(session):
    characters.save_identity(session, "u1", name="Bob", char_class="Wizard",
                             alignment="neutral")
    characters.update_stats(
        session, "u1", scores=[15, 14, 10, 16, 10, 10], level=2, max_hp=8,
        armor_class=11, spell_ability="int", spell_check_bonus=1,
    )
    characters.set_gold(session, "u1", 1050)
    characters.carry(session, "u1", item_name="Arrow", quantity=25)
    characters.carry(session, "u1", item_name="Idol", quantity=1,
                     freeform_slots=0.5, notes="Creepy")
    characters.carry(session, "u1", item_name="Rope", quantity=1, role="stash")
    characters.learn_spell(session, "u1", "Fireball")
    characters.learn_spell(session, "u1", "Light")


# ---------- public endpoints ----------


def test_health_needs_no_login(api):
    assert api.get("/api/health").json() == {"ok": True}


def test_auth_config_exposes_no_secret(api):
    body = api.get("/api/auth/config").json()
    assert body == {
        "client_id": "app123",
        "redirect_uri": BASE + "/auth/callback",
        "scopes": ["identify"],
    }


def test_docs_are_not_published(api):
    assert api.get("/docs").status_code == 404
    assert api.get("/openapi.json").status_code == 404


# ---------- login ----------


def test_web_login_uses_redirect_uri_and_hides_discord_token(api):
    body = login(api, "u1", flow="web")
    assert body["user"] == {"id": "u1", "display_name": "User u1", "avatar_url": None}
    assert body["expires_in"] == 3600
    assert body["discord_access_token"] is None
    assert api.oauth.redirect_uris == [BASE + "/auth/callback"]


def test_activity_login_returns_discord_token_for_sdk(api):
    body = login(api, "u1", flow="activity")
    assert body["discord_access_token"] == "token-u1"
    assert api.oauth.redirect_uris == [None]  # SDK codes carry no redirect URI


def test_bad_code_is_401(api):
    resp = api.post("/api/auth/exchange", json={"code": "nope"})
    assert resp.status_code == 401


def test_non_member_is_403(api):
    resp = api.post("/api/auth/exchange", json={"code": "good-stranger"})
    assert resp.status_code == 403
    assert "members of our Discord servers" in resp.json()["detail"]


def test_endpoints_require_a_valid_token(api):
    assert api.get("/api/me").status_code == 401
    assert api.get("/api/me", headers=auth("garbage")).status_code == 401
    assert api.get("/api/me", headers={"Authorization": "Basic abc"}).status_code == 401
    forged = SessionSigner("y" * 40, 3600).issue(DiscordUser(id="u1", username="x"))
    assert api.get("/api/me", headers=auth(forged)).status_code == 401


def test_expired_token_is_rejected():
    signer = SessionSigner(SECRET, ttl_seconds=-1)
    token = signer.issue(DiscordUser(id="u1", username="bob"))
    assert signer.verify(token) is None
    assert SessionSigner(SECRET, 60).verify(token).user_id == "u1"


# ---------- read endpoints ----------


def test_me_reports_whether_a_character_exists(api):
    token = login(api, "u1")["token"]
    assert api.get("/api/me", headers=auth(token)).json() == {
        "user_id": "u1", "display_name": "User u1", "has_character": False,
    }
    assert api.get("/api/character", headers=auth(token)).status_code == 404
    with session_scope() as s:
        characters.save_identity(s, "u1", name="Bob")
    assert api.get("/api/me", headers=auth(token)).json()["has_character"] is True


def test_my_character_full_sheet(api):
    with session_scope() as s:
        _seed(s)
        _make_bob(s)
    token = login(api, "u1")["token"]
    sheet = api.get("/api/character", headers=auth(token)).json()

    assert (sheet["name"], sheet["char_class"], sheet["alignment"]) == (
        "Bob", "Wizard", "Neutral"
    )
    abilities = {a["key"]: (a["score"], a["modifier"]) for a in sheet["abilities"]}
    assert abilities["str"] == (15, 2) and abilities["int"] == (16, 3)
    # AC = base 11 + DEX(14) modifier +2, as the bot shows it
    assert (sheet["armor_class_base"], sheet["armor_class"]) == (11, 13)
    assert sheet["spellcasting"] == {
        "ability": "int", "ability_modifier": 3, "talent_bonus": 1, "total": 4,
    }
    assert (sheet["gold_cp"], sheet["gold"]) == (1050, "10gp 5sp")
    # spells sorted by tier then name, with reference text attached
    assert [sp["name"] for sp in sheet["spells"]] == ["Light", "Fireball"]
    assert sheet["spells"][0]["spell"]["duration"] == "1 hour"

    held = sheet["held"]
    assert (held["name"], held["max_slots"], held["used_slots"]) == ("Bob", 15, 2.5)
    by_name = {i["name"]: i for i in held["items"]}
    assert by_name["Arrow"]["quantity"] == 25 and by_name["Arrow"]["slot_cost"] == 2
    assert by_name["Idol"]["source"] == "freeform"
    assert by_name["Idol"]["description"] == "Creepy"
    stash = sheet["stash"]
    assert stash["name"] == "Bob Guild Stash"
    assert stash["items"][0]["description"] == "50 feet"
    assert stash["items"][0]["value_cp"] == 100


def test_other_players_sheet_hides_stash(api):
    with session_scope() as s:
        _seed(s)
        _make_bob(s)
    token = login(api, "u2")["token"]
    sheet = api.get("/api/characters/u1", headers=auth(token)).json()
    assert sheet["name"] == "Bob"
    assert sheet["stash"] is None
    assert {i["name"] for i in sheet["held"]["items"]} == {"Arrow", "Idol"}
    missing = api.get("/api/characters/nobody", headers=auth(token))
    assert missing.status_code == 404


def test_spell_and_item_reference(api):
    with session_scope() as s:
        _seed(s)
    token = login(api, "u1")["token"]
    names = lambda r: [x["name"] for x in r.json()]  # noqa: E731
    assert names(api.get("/api/spells", headers=auth(token))) == [
        "Cure Wounds", "Light", "Fireball"
    ]
    assert names(api.get("/api/spells?class=wizard", headers=auth(token))) == [
        "Light", "Fireball"
    ]
    assert names(api.get("/api/spells?class=priest&tier=1", headers=auth(token))) == [
        "Cure Wounds", "Light"
    ]
    assert api.get("/api/spells?class=bard", headers=auth(token)).status_code == 422
    assert names(api.get("/api/items?q=ro", headers=auth(token))) == ["Arrow", "Rope"]
    assert names(api.get("/api/items?q=rope", headers=auth(token))) == ["Rope"]


# ---------- real DiscordOAuth against a mocked Discord ----------


def _discord_transport(*, token_status=200, user_status=200):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            seen["form"] = dict(httpx.QueryParams(request.content.decode()))
            if token_status != 200:
                return httpx.Response(token_status, json={"error": "invalid_grant"})
            return httpx.Response(200, json={"access_token": "abc", "token_type": "Bearer"})
        if request.url.path.endswith("/users/@me"):
            seen["auth"] = request.headers["authorization"]
            if user_status != 200:
                return httpx.Response(user_status)
            return httpx.Response(
                200, json={"id": "42", "username": "bob", "global_name": None,
                           "avatar": "hash"},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler), seen


def test_discord_oauth_exchange_and_user():
    import asyncio

    transport, seen = _discord_transport()
    oauth = DiscordOAuth("app123", "shh", transport=transport)
    token = asyncio.run(oauth.exchange_code("c0de", BASE + "/auth/callback"))
    user = asyncio.run(oauth.fetch_user(token))
    assert token == "abc"
    assert seen["form"] == {
        "client_id": "app123", "client_secret": "shh",
        "grant_type": "authorization_code", "code": "c0de",
        "redirect_uri": BASE + "/auth/callback",
    }
    assert seen["auth"] == "Bearer abc"
    assert (user.id, user.display_name) == ("42", "bob")
    assert user.avatar_url == "https://cdn.discordapp.com/avatars/42/hash.png"

    asyncio.run(oauth.exchange_code("c0de", None))
    assert "redirect_uri" not in seen["form"]


def test_discord_oauth_errors():
    import asyncio

    bad_code = DiscordOAuth("a", "b", transport=_discord_transport(token_status=400)[0])
    with pytest.raises(OAuthError):
        asyncio.run(bad_code.exchange_code("x", None))
    bad_user = DiscordOAuth("a", "b", transport=_discord_transport(user_status=401)[0])
    with pytest.raises(OAuthError):
        asyncio.run(bad_user.fetch_user("x"))


# ---------- guild allowlist via the bot ----------


class _Resp:
    status = 404
    reason = "Not Found"


class _Guild:
    def __init__(self, cached=(), fetchable=()):
        self.cached, self.fetchable, self.fetched = set(cached), set(fetchable), []

    def get_member(self, uid):
        return object() if uid in self.cached else None

    async def fetch_member(self, uid):
        import discord

        self.fetched.append(uid)
        if uid not in self.fetchable:
            raise discord.NotFound(_Resp(), "Unknown Member")
        return object()


class _Bot:
    def __init__(self, guilds):
        self.guilds = guilds

    async def wait_until_ready(self):
        return None

    def get_guild(self, gid):
        return self.guilds.get(gid)


def test_bot_membership_check():
    import asyncio

    from shadowdark_bot.web.server import bot_membership_check

    allowed, other = _Guild(cached={1}, fetchable={2}), _Guild(fetchable={3})
    bot = _Bot({100: allowed, 200: other})
    check = bot_membership_check(bot, {100, 999})  # 999: bot isn't in it
    assert asyncio.run(check("1")) is True  # cached member
    assert asyncio.run(check("2")) is True  # found via REST
    assert asyncio.run(check("3")) is False  # only in a guild that isn't allowed
    assert other.fetched == []  # never consulted


def test_web_config_errors():
    from shadowdark_bot.config import Settings

    ok = Settings(
        DISCORD_TOKEN="t", PUBLIC_BASE_URL=BASE, DISCORD_CLIENT_ID="1",
        DISCORD_CLIENT_SECRET="s", SESSION_SECRET=SECRET, ALLOWED_GUILD_IDS="1, 2",
    )
    assert ok.web_config_errors() == []
    assert ok.allowed_guild_ids == {1, 2}
    bad = ok.model_copy(update={"SESSION_SECRET": "short", "ALLOWED_GUILD_IDS": ""})
    errors = " ".join(bad.web_config_errors())
    assert "SESSION_SECRET" in errors and "ALLOWED_GUILD_IDS" in errors
    junk = ok.model_copy(update={"ALLOWED_GUILD_IDS": "abc"})
    assert "numeric" in " ".join(junk.web_config_errors())
