"""Tests for the web API: login exchange, session tokens, the guild allowlist,
and the read endpoints. Discord is faked; the database is the in-memory test DB."""

import httpx
import pytest
from fastapi.testclient import TestClient

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import ITEM_TYPE_COMMON, Item, Location, Spell
from shadowdark_bot.services import characters
from shadowdark_bot.services.coffers import get_or_create_coffer
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
        mode="discord",
        oauth=oauth,
        signer=SessionSigner(SECRET, ttl_seconds=3600),
        is_allowed_member=is_member,
        public_base_url=BASE + "/",
        static_dir=None,
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
        "mode": "discord",
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
    forged = SessionSigner("y" * 40, 3600).issue("u1", "x")
    assert api.get("/api/me", headers=auth(forged)).status_code == 401


def test_expired_token_is_rejected():
    signer = SessionSigner(SECRET, ttl_seconds=-1)
    token = signer.issue("u1", "bob")
    assert signer.verify(token) is None
    assert SessionSigner(SECRET, 60).verify(token).user_id == "u1"


# ---------- read endpoints ----------


def test_me_reports_whether_a_character_exists(api):
    token = login(api, "u1")["token"]
    assert api.get("/api/me", headers=auth(token)).json() == {
        "user_id": "u1", "display_name": "User u1", "has_character": False,
        "is_dm": False,
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


def test_sheet_languages_are_deduplicated(api):
    with session_scope() as s:
        _seed(s)
        _make_bob(s)
        characters.set_skills_knowledge(
            s, "u1", languages="Common, common", talents=None, additional_info=None
        )
    token = login(api, "u1")["token"]
    sheet = api.get("/api/character", headers=auth(token)).json()
    assert sheet["languages"] == "Common"


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

    assert Settings(DISCORD_TOKEN="t", LOCAL_WEB_ENABLED=True).web_config_errors() == []
    ok = Settings(
        DISCORD_TOKEN="t", WEB_ENABLED=True, PUBLIC_BASE_URL=BASE, DISCORD_CLIENT_ID="1",
        DISCORD_CLIENT_SECRET="s", SESSION_SECRET=SECRET, ALLOWED_GUILD_IDS="1, 2",
    )
    assert ok.web_config_errors() == []
    assert ok.allowed_guild_ids == {1, 2}
    bad = ok.model_copy(update={"SESSION_SECRET": "short", "ALLOWED_GUILD_IDS": ""})
    errors = " ".join(bad.web_config_errors())
    assert "SESSION_SECRET" in errors and "ALLOWED_GUILD_IDS" in errors
    junk = ok.model_copy(update={"ALLOWED_GUILD_IDS": "abc"})
    assert "numeric" in " ".join(junk.web_config_errors())
    clash = ok.model_copy(update={"LOCAL_WEB_ENABLED": True, "LOCAL_WEB_PORT": 8080})
    assert "must differ" in " ".join(clash.web_config_errors())
    star = ok.model_copy(update={"FORWARDED_ALLOW_IPS": "*"})
    assert "not *" in " ".join(star.web_config_errors())
    for fine in ("192.168.1.11", "127.0.0.1, 192.168.1.11"):
        assert ok.model_copy(update={"FORWARDED_ALLOW_IPS": fine}).web_config_errors() == []
    off = ok.model_copy(update={"WEB_ENABLED": False, "FORWARDED_ALLOW_IPS": "*"})
    assert off.web_config_errors() == []


def test_proxy_headers_public_only():
    from shadowdark_bot.config import Settings
    from shadowdark_bot.web.server import WebServer

    ok = Settings(
        DISCORD_TOKEN="t", WEB_ENABLED=True, PUBLIC_BASE_URL=BASE, DISCORD_CLIENT_ID="1",
        DISCORD_CLIENT_SECRET="s", SESSION_SECRET=SECRET, ALLOWED_GUILD_IDS="1",
        FORWARDED_ALLOW_IPS="192.168.1.11",
    )
    public = WebServer.public(_Bot({}), ok)._server.config
    assert public.proxy_headers is True
    assert public.forwarded_allow_ips == "192.168.1.11"
    assert WebServer.local(ok)._server.config.proxy_headers is False


# ---------- local (home network, no login) mode ----------

LAN = ("192.168.1.50", 50000)


@pytest.fixture
def local_api(dbsession):
    app = create_app(
        mode="local", signer=SessionSigner(SECRET, 3600, purpose="local"), static_dir=None
    )
    return TestClient(app, client=LAN)


def test_local_mode_picks_a_character_without_discord(local_api):
    with session_scope() as s:
        _seed(s)
        _make_bob(s)
        characters.save_identity(s, "u2", name="Amy", char_class="Thief")
    assert local_api.get("/api/auth/config").json()["mode"] == "local"
    picks = local_api.get("/api/auth/local/characters").json()
    assert [(c["user_id"], c["name"]) for c in picks] == [("u2", "Amy"), ("u1", "Bob")]

    body = local_api.post("/api/auth/local", json={"user_id": "u1"}).json()
    assert body["user"] == {"id": "u1", "display_name": "Bob", "avatar_url": None}
    sheet = local_api.get("/api/character", headers=auth(body["token"])).json()
    assert sheet["name"] == "Bob" and sheet["stash"] is not None
    party = local_api.get("/api/characters", headers=auth(body["token"])).json()
    assert len(party) == 2


def test_local_mode_has_no_discord_login_and_still_needs_a_session(local_api):
    assert local_api.post("/api/auth/exchange", json={"code": "good-u1"}).status_code == 404
    assert local_api.get("/api/character").status_code == 401
    missing = local_api.post("/api/auth/local", json={"user_id": "nobody"})
    assert missing.status_code == 404


def test_local_mode_refuses_proxied_or_public_requests(dbsession):
    app = create_app(mode="local", signer=SessionSigner(SECRET, 60), static_dir=None)
    lan = TestClient(app, client=LAN)
    assert lan.get("/api/health").status_code == 200
    assert TestClient(app, client=("127.0.0.1", 1)).get("/api/health").status_code == 200
    assert TestClient(app, client=("::1", 1)).get("/api/health").status_code == 200
    # through a reverse proxy (NPM adds these) -> refused
    for header in ("X-Forwarded-For", "X-Real-IP", "Forwarded"):
        resp = lan.get("/api/health", headers={header: "203.0.113.9"})
        assert resp.status_code == 403, header
    # straight from a public address -> refused
    public = TestClient(app, client=("8.8.8.8", 1))
    assert public.get("/api/auth/local/characters").status_code == 403


def test_local_tokens_never_work_on_the_public_app(api, local_api):
    """Same secret on purpose: the purpose salt alone must keep them apart."""
    with session_scope() as s:
        characters.save_identity(s, "u1", name="Bob")
    local_token = local_api.post("/api/auth/local", json={"user_id": "u1"}).json()["token"]
    assert api.get("/api/character", headers=auth(local_token)).status_code == 401
    public_token = login(api, "u1")["token"]
    assert local_api.get("/api/character", headers=auth(public_token)).status_code == 401


def test_build_local_app_uses_a_fresh_random_key(dbsession):
    from shadowdark_bot.web.server import build_local_app

    with session_scope() as s:
        characters.save_identity(s, "u1", name="Bob")
    a = TestClient(build_local_app(), client=LAN)
    b = TestClient(build_local_app(), client=LAN)
    token = a.post("/api/auth/local", json={"user_id": "u1"}).json()["token"]
    assert a.get("/api/me", headers=auth(token)).status_code == 200
    assert b.get("/api/me", headers=auth(token)).status_code == 401  # restarted app


# ---------- frontend serving ----------


def test_frontend_is_served_for_non_api_paths(dbsession):
    app = create_app(mode="local", signer=SessionSigner(SECRET, 60))
    client = TestClient(app, client=LAN)
    for path in ("/", "/auth/callback", "/anything/else"):
        resp = client.get(path)
        assert resp.status_code == 200, path
        assert "<title>Shadowdark Sheet</title>" in resp.text
        assert resp.headers["cache-control"] == "no-cache"
        assert "x-frame-options" not in resp.headers  # Discord must be able to embed it
    js = client.get("/assets/app.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert client.get("/assets/nope.js").status_code == 404
    # the Activity login's vendored SDK (one ES module, no build step)
    sdk = client.get("/assets/vendor/discord-sdk.js")
    assert sdk.status_code == 200 and "javascript" in sdk.headers["content-type"]
    assert "DiscordSDK" in sdk.text
    # unknown API paths stay JSON 404s instead of falling through to the page
    resp = client.get("/api/nope")
    assert resp.status_code == 404 and resp.json() == {"detail": "Not found."}


# ---------- guild / catalog ----------


def test_guild_overview(api):
    from shadowdark_bot.models import (
        ITEM_TYPE_MAGICAL,
        Borrow,
        Coffer,
        InventoryEntry,
        Location,
        TreasuryEntry,
    )

    with session_scope() as s:
        _seed(s)
        _make_bob(s)  # Bob's held/stash must NOT appear as guild storage
        rope = s.query(Item).filter_by(name="Rope").one()
        amulet = Item(name="Amulet", gear_slots=1, item_type=ITEM_TYPE_MAGICAL,
                      description="Glows near undead.", value_cp=10000)
        armory = Location(name="Armory", kind="inventory", max_gear_slots=20,
                          description="Behind the tavern")
        vault = Location(name="Treasury", kind="treasury", max_gear_slots=0)
        s.add_all([amulet, armory, vault, Coffer(balance_cp=12345)])
        s.flush()
        s.add(InventoryEntry(location_id=armory.id, item_id=rope.id, quantity=3))
        lent = TreasuryEntry(location_id=vault.id, item_id=amulet.id, tag="cracked",
                             status="borrowed")
        spare = TreasuryEntry(location_id=vault.id, item_id=amulet.id, status="available")
        s.add_all([lent, spare])
        s.flush()
        s.add(Borrow(treasury_entry_id=lent.id, borrower_id="u1", notes="for the crypt"))
        s.add(Borrow(treasury_entry_id=spare.id, borrower_id="u2",
                     returned_at=lent.added_at))  # old, returned loan
    token = login(api, "u2")["token"]
    g = api.get("/api/guild", headers=auth(token)).json()

    assert g["coffers"] == {"balance_cp": 12345, "balance": "123gp 4sp 5cp"}
    assert [loc["name"] for loc in g["locations"]] == ["Armory"]
    armory = g["locations"][0]
    assert armory["description"] == "Behind the tavern"
    assert (armory["used_slots"], armory["max_slots"]) == (3, 20)
    assert armory["items"][0]["name"] == "Rope"

    by_tag = {t["tag"]: t for t in g["treasury"]}
    assert by_tag["cracked"]["status"] == "borrowed"
    assert by_tag["cracked"]["borrower"]["name"] == "Bob"
    assert by_tag["cracked"]["borrower"]["notes"] == "for the crypt"
    assert by_tag["cracked"]["item"]["description"] == "Glows near undead."
    assert by_tag[None]["status"] == "available" and by_tag[None]["borrower"] is None
    assert api.get("/api/guild").status_code == 401


def test_guild_overview_empty(api):
    token = login(api, "u1")["token"]
    g = api.get("/api/guild", headers=auth(token)).json()
    assert g == {"coffers": {"balance_cp": 0, "balance": "0cp"}, "locations": [], "treasury": [], "party": []}


def test_item_catalog_type_filter_and_limit(api):
    from shadowdark_bot.models import ITEM_TYPE_WEAPON

    with session_scope() as s:
        _seed(s)
        s.add(Item(name="Bow", gear_slots=1, item_type=ITEM_TYPE_WEAPON))
    token = login(api, "u1")["token"]
    names = lambda r: [x["name"] for x in r.json()]  # noqa: E731
    assert names(api.get("/api/items?type=weapon", headers=auth(token))) == ["Bow"]
    assert len(api.get("/api/items?limit=1000", headers=auth(token)).json()) == 3
    assert api.get("/api/items?type=banana", headers=auth(token)).status_code == 422
    assert api.get("/api/items?limit=5000", headers=auth(token)).status_code == 422


# ---------- editing ----------


def _bob_client(api):
    with session_scope() as s:
        _seed(s)
        _make_bob(s)
    return api, auth(login(api, "u1")["token"])


def _stack(sheet, where, name):
    return next(i for i in sheet[where]["items"] if i["name"] == name)


def test_edit_gold(api):
    c, h = _bob_client(api)
    r = c.patch("/api/character/gold", json={"gold": "3gp"}, headers=h)
    assert r.status_code == 200
    assert r.json()["gold_cp"] == 300 and r.json()["gold"] == "3gp"
    r = c.patch("/api/character/gold", json={"gold": "12gp 5sp 3cp"}, headers=h)
    assert r.json()["gold_cp"] == 1253
    assert c.patch("/api/character/gold", json={"gold": ""}, headers=h).json()["gold_cp"] == 0
    bad = c.patch("/api/character/gold", json={"gold": "lots"}, headers=h)
    assert bad.status_code == 400 and bad.json()["detail"]
    assert c.patch("/api/character/gold", json={"gold": "1050"}, headers=h).status_code == 400
    assert c.patch("/api/character/gold", json={"gold": 5}, headers=h).status_code == 422
    other = auth(login(c, "u2")["token"])  # u2 has no character
    assert c.patch("/api/character/gold", json={"gold": "1gp"}, headers=other).status_code == 404


def test_edit_requires_login(api):
    _bob_client(api)
    assert api.patch("/api/character/gold", json={"gold": "1gp"}).status_code == 401
    assert api.post("/api/character/items", json={"name": "Rope"}).status_code == 401
    assert api.delete("/api/character/spells/1").status_code == 401


def test_carry_items(api):
    c, h = _bob_client(api)
    r = c.post("/api/character/items", json={"name": "Rope"}, headers=h)
    assert r.status_code == 200
    rope = _stack(r.json(), "held", "Rope")
    assert rope["source"] == "catalog" and rope["quantity"] == 1
    r = c.post("/api/character/items", json={"name": "Rope", "to": "stash"}, headers=h)
    assert _stack(r.json(), "stash", "Rope")["quantity"] == 2
    r = c.post("/api/character/items", json={"name": "Lamp", "slots_each": 0.5,
                                             "notes": "oil"}, headers=h)
    assert _stack(r.json(), "held", "Lamp")["source"] == "freeform"
    assert c.post("/api/character/items", json={"name": ""}, headers=h).status_code == 400
    assert c.post("/api/character/items", json={"name": "Rope", "quantity": 0},
                  headers=h).status_code == 400
    big = c.post("/api/character/items", json={"name": "Rope", "quantity": 30}, headers=h)
    assert big.status_code == 400 and big.json()["detail"].startswith("Your held items holds")
    assert c.post("/api/character/items", json={"name": "Rope", "to": "x"},
                  headers=h).status_code == 422
    # the failed carries changed nothing
    sheet = c.get("/api/character", headers=h).json()
    assert _stack(sheet, "held", "Rope")["quantity"] == 1


def test_drop_items(api):
    c, h = _bob_client(api)
    sheet = c.get("/api/character", headers=h).json()
    arrow = _stack(sheet, "held", "Arrow")["id"]
    r = c.delete(f"/api/character/items/{arrow}?quantity=5", headers=h)
    assert r.status_code == 200 and _stack(r.json(), "held", "Arrow")["quantity"] == 20
    over = c.delete(f"/api/character/items/{arrow}?quantity=99", headers=h)
    assert over.status_code == 400 and "You only have" in over.json()["detail"]
    r = c.delete(f"/api/character/items/{arrow}", headers=h)
    assert r.status_code == 200
    assert all(i["name"] != "Arrow" for i in r.json()["held"]["items"])
    assert c.delete(f"/api/character/items/{arrow}", headers=h).status_code == 404
    assert c.delete("/api/character/items/99999", headers=h).status_code == 404
    # another user's entry id is a 404, and it is untouched
    with session_scope() as s:
        characters.save_identity(s, "u2", name="Alice")
        characters.carry(s, "u2", item_name="Rope", quantity=1)
    alice = c.get("/api/characters/u2", headers=h).json()
    rope = _stack(alice, "held", "Rope")["id"]
    assert c.delete(f"/api/character/items/{rope}", headers=h).status_code == 404
    assert _stack(c.get("/api/characters/u2", headers=h).json(), "held", "Rope")


def test_give_targets_and_give(api):
    c, h = _bob_client(api)
    from shadowdark_bot.models import Location

    with session_scope() as s:
        characters.save_identity(s, "u2", name="Alice")
        s.add(Location(name="Armory", kind="inventory", max_gear_slots=20))
    c.get("/api/characters/u2", headers=h)  # makes sure Alice's locations exist
    sheet = c.get("/api/character", headers=h).json()
    arrow = _stack(sheet, "held", "Arrow")["id"]
    idol = _stack(sheet, "held", "Idol")["id"]

    targets = c.get(f"/api/character/items/{arrow}/give-targets", headers=h).json()
    kinds = [t["kind"] for t in targets]
    assert kinds[0] == "guild" and targets[0]["name"] == "Armory"
    assert targets[0]["owner_user_id"] is None
    assert "self" in kinds and "character" in kinds
    alice = next(t for t in targets if t["kind"] == "character")
    assert alice["owner_user_id"] == "u2"
    assert c.get("/api/character/items/99999/give-targets", headers=h).status_code == 404

    r = c.post(f"/api/character/items/{arrow}/give",
               json={"target_location_id": alice["location_id"], "quantity": 5}, headers=h)
    assert r.status_code == 200
    assert _stack(r.json(), "held", "Arrow")["quantity"] == 20
    assert _stack(c.get("/api/characters/u2", headers=h).json(), "held", "Arrow")["quantity"] == 5

    # held to own stash
    mine = next(t for t in targets if t["kind"] == "self")
    r = c.post(f"/api/character/items/{arrow}/give",
               json={"target_location_id": mine["location_id"]}, headers=h)
    assert r.status_code == 200 and _stack(r.json(), "stash", "Arrow")["quantity"] == 20

    bad = c.post(f"/api/character/items/{idol}/give",
                 json={"target_location_id": targets[0]["location_id"]}, headers=h)
    assert bad.status_code == 400 and "Freeform" in bad.json()["detail"]
    assert c.post("/api/character/items/99999/give",
                  json={"target_location_id": 1}, headers=h).status_code == 404
    gone = c.post(f"/api/character/items/{idol}/give",
                  json={"target_location_id": 99999}, headers=h)
    assert gone.status_code == 400


def test_learn_and_forget_spells(api):
    c, h = _bob_client(api)
    spells = {s["name"]: s["id"] for s in c.get("/api/spells", headers=h).json()}
    light = c.post("/api/character/spells", json={"spell_id": spells["Light"]}, headers=h)
    assert light.status_code == 400 and "You already know" in light.json()["detail"]
    cure = c.post("/api/character/spells", json={"spell_id": spells["Cure Wounds"]}, headers=h)
    assert cure.status_code == 400
    assert c.post("/api/character/spells", json={"spell_id": 99999}, headers=h).status_code == 404

    sheet = c.get("/api/character", headers=h).json()
    known = next(k for k in sheet["spells"] if k["name"] == "Fireball")["id"]
    r = c.delete(f"/api/character/spells/{known}", headers=h)
    assert r.status_code == 200 and [k["name"] for k in r.json()["spells"]] == ["Light"]
    assert c.delete(f"/api/character/spells/{known}", headers=h).status_code == 404
    r = c.post("/api/character/spells", json={"spell_id": spells["Fireball"]}, headers=h)
    assert r.status_code == 200 and len(r.json()["spells"]) == 2


def test_edit_in_local_mode(local_api):
    with session_scope() as s:
        _seed(s)
        _make_bob(s)
    token = local_api.post("/api/auth/local", json={"user_id": "u1"}).json()["token"]
    r = local_api.patch("/api/character/gold", json={"gold": "7gp"}, headers=auth(token))
    assert r.status_code == 200 and r.json()["gold"] == "7gp"
    with session_scope() as s:
        assert characters.require_character(s, "u1").gold_cp == 700


def test_edit_identity_stats_skills_proficiencies(api):
    c, h = _bob_client(api)
    r = c.patch("/api/character/identity", headers=h, json={
        "name": "Robert", "char_class": "Wizard", "title": "Apprentice",
        "ancestry": "Human", "alignment": "Lawful", "background": "Urchin"})
    assert r.status_code == 200
    assert (r.json()["name"], r.json()["alignment"], r.json()["title"]) == (
        "Robert", "Lawful", "Apprentice")
    assert c.patch("/api/character/identity", json={"name": " "}, headers=h).status_code == 400
    assert c.patch("/api/character/identity", json={"name": "B", "alignment": "evil"},
                   headers=h).status_code == 400
    # no character creation over the web
    other = auth(login(c, "u2")["token"])
    assert c.patch("/api/character/identity", json={"name": "Eve"},
                   headers=other).status_code == 404

    sheet = c.get("/api/character", headers=h).json()
    base_ac = sheet["armor_class_base"]
    body = {"scores": [a["score"] for a in sheet["abilities"]], "level": 3, "max_hp": 12,
            "armor_class": base_ac, "spell_ability": "INT", "spell_check_bonus": 1}
    r = c.patch("/api/character/stats", json=body, headers=h)
    assert r.status_code == 200
    assert r.json()["level"] == 3 and r.json()["armor_class_base"] == base_ac
    assert r.json()["spellcasting"]["ability"] == "int"
    r = c.patch("/api/character/stats", json={**body, "spell_ability": "none"}, headers=h)
    assert r.json()["spellcasting"] is None
    bad = c.patch("/api/character/stats", json={**body, "scores": [31, 1, 1, 1, 1, 1]},
                  headers=h)
    assert bad.status_code == 400 and "out of range" in bad.json()["detail"]

    r = c.patch("/api/character/skills", headers=h,
                json={"languages": "Common, Elvish", "talents": "Keen"})
    assert r.json()["languages"] == "Common, Elvish" and r.json()["additional_info"] is None
    r = c.patch("/api/character/proficiencies", json={"proficiencies": "Daggers"}, headers=h)
    assert r.json()["proficiencies"] == "Daggers"
    r = c.patch("/api/character/proficiencies", json={}, headers=h)
    assert r.json()["proficiencies"] is None


# ---------- edit a stack / buy ----------


def test_edit_item_endpoint(api):
    c, h = _bob_client(api)
    sheet = c.get("/api/character", headers=h).json()
    idol = _stack(sheet, "held", "Idol")["id"]
    rope = _stack(sheet, "stash", "Rope")["id"]
    r = c.patch(f"/api/character/items/{idol}", headers=h,
                json={"name": "Gold Idol", "slots_each": 2, "quantity": 2, "notes": "heavy"})
    assert r.status_code == 200
    gold = _stack(r.json(), "held", "Gold Idol")
    assert (gold["quantity"], gold["gear_slots_each"], gold["description"]) == (2, 2, "heavy")
    # catalog stacks: quantity and notes only
    r = c.patch(f"/api/character/items/{rope}", headers=h, json={"quantity": 3, "notes": "hemp"})
    rope_out = _stack(r.json(), "stash", "Rope")
    assert (rope_out["quantity"], rope_out["notes"]) == (3, "hemp")
    bad = c.patch(f"/api/character/items/{rope}", headers=h, json={"name": "Cord"})
    assert bad.status_code == 400 and "can't be renamed" in bad.json()["detail"]
    over = c.patch(f"/api/character/items/{idol}", headers=h, json={"quantity": 40})
    assert over.status_code == 400 and "would need" in over.json()["detail"]
    assert c.patch("/api/character/items/9999", headers=h, json={"quantity": 1}).status_code == 404
    # someone else's stack is not yours to edit
    _make_amy(c)
    amy = auth(login(c, "u2")["token"])
    assert c.patch(f"/api/character/items/{idol}", headers=amy,
                   json={"quantity": 1}).status_code == 404
    assert c.patch(f"/api/character/items/{idol}", json={"quantity": 1}).status_code == 401


def _make_amy(client):
    with session_scope() as s:
        characters.save_identity(s, "u2", name="Amy")


def test_buy_endpoint(api):
    c, h = _bob_client(api)
    with session_scope() as s:
        torch = Item(name="Torch", gear_slots=1, bundle_size=1,
                     item_type=ITEM_TYPE_COMMON, value_cp=10)
        cellar = Location(name="Cellar", kind="inventory", max_gear_slots=5.0)
        s.add_all([torch, cellar])
        s.flush()
        torch_id, cellar_id = torch.id, cellar.id
        get_or_create_coffer(s).balance_cp = 50
    sheet = c.get("/api/character", headers=h).json()
    held_id = sheet["held"]["location_id"]

    r = c.post(f"/api/items/{torch_id}/buy", headers=h, json={
        "quantity": 2, "pay_from": "character", "destination_location_id": held_id})
    assert r.status_code == 200
    assert r.json()["gold_cp"] == 1030 and _stack(r.json(), "held", "Torch")["quantity"] == 2

    r = c.post(f"/api/items/{torch_id}/buy", headers=h, json={
        "quantity": 3, "pay_from": "guild", "destination_location_id": cellar_id})
    assert r.status_code == 200 and r.json()["gold_cp"] == 1030  # own gold untouched
    guild = c.get("/api/guild", headers=h).json()
    assert guild["coffers"]["balance_cp"] == 20
    assert guild["locations"][0]["items"][0]["quantity"] == 3

    short = c.post(f"/api/items/{torch_id}/buy", headers=h, json={
        "quantity": 3, "pay_from": "guild", "destination_location_id": cellar_id})
    assert short.status_code == 400 and "Short by" in short.json()["detail"]
    full = c.post(f"/api/items/{torch_id}/buy", headers=h, json={
        "quantity": 99, "pay_from": "character", "destination_location_id": held_id})
    assert full.status_code == 400 and "would need" in full.json()["detail"]
    assert c.get("/api/character", headers=h).json()["gold_cp"] == 1030
    assert c.post(f"/api/items/{torch_id}/buy", headers=h, json={
        "quantity": 1, "pay_from": "bank", "destination_location_id": held_id}).status_code == 422
    assert c.post("/api/items/9999/buy", headers=h, json={
        "quantity": 1, "pay_from": "guild", "destination_location_id": held_id}).status_code == 400
    assert c.post(f"/api/items/{torch_id}/buy", json={
        "quantity": 1, "pay_from": "guild", "destination_location_id": held_id}).status_code == 401


# ---------- effects and lost spells ----------


def test_effects_api(api):
    c, h = _bob_client(api)
    assert c.get("/api/character", headers=h).json()["effects"] == []
    r = c.post("/api/character/effects", headers=h, json={"name": "Light", "max_rounds": 6})
    assert r.status_code == 200
    eff = r.json()["effects"][0]
    assert (eff["name"], eff["max_rounds"], eff["rounds_left"], eff["ended"]) == ("Light", 6, 6, False)
    url = f"/api/character/effects/{eff['id']}"
    r = c.patch(url, headers=h, json={"rounds_used": 3})
    assert r.json()["effects"][0]["rounds_left"] == 3
    assert c.patch(url, headers=h, json={"rounds_used": 7}).status_code == 400
    assert c.patch(url, headers=h, json={"rounds_used": -1}).status_code == 400
    r = c.patch(url, headers=h, json={"rounds_used": 6})
    assert r.json()["effects"][0]["ended"] is True
    r = c.patch(url, headers=h, json={"rounds_used": 5})
    assert r.json()["effects"][0]["ended"] is False
    assert c.post("/api/character/effects", headers=h, json={"name": " ", "max_rounds": 3}).status_code == 400
    assert c.post("/api/character/effects", headers=h, json={"name": "x", "max_rounds": 101}).status_code == 400
    assert c.post("/api/character/effects", headers=h, json={"name": "x", "bonuses": {"ac": "huh"}}).status_code == 400
    r = c.patch(url, headers=h, json={"name": "Torchlight", "bonuses": {"ac": "=14"}})
    assert r.json()["effects"][0]["name"] == "Torchlight"
    assert r.json()["effective"]["ac"]["total"] == 14
    assert r.json()["armor_class"] == 13  # base fields untouched

    # another user can neither see nor touch it
    other = auth(login(c, "u2")["token"])
    assert c.patch(url, headers=other, json={"rounds_used": 1}).status_code == 404
    assert c.delete(url, headers=other).status_code == 404

    r = c.delete(url, headers=h)
    assert r.status_code == 200 and r.json()["effects"] == []
    assert r.json()["effective"]["ac"]["total"] == 13
    assert c.delete(url, headers=h).status_code == 404


def test_effects_visible_on_party_view(api):
    c, h = _bob_client(api)
    c.post("/api/character/effects", headers=h, json={"name": "Light", "max_rounds": 6})
    other = auth(login(c, "u2")["token"])
    sheet = c.get("/api/characters/u1", headers=other).json()
    assert [e["name"] for e in sheet["effects"]] == ["Light"]


def test_spell_lost_and_rest_api(api):
    c, h = _bob_client(api)
    sheet = c.get("/api/character", headers=h).json()
    assert [k["lost"] for k in sheet["spells"]] == [False, False]
    ids = [k["id"] for k in sheet["spells"]]
    r = c.patch(f"/api/character/spells/{ids[0]}", headers=h, json={"lost": True})
    assert r.status_code == 200 and [k["lost"] for k in r.json()["spells"]] == [True, False]
    c.patch(f"/api/character/spells/{ids[1]}", headers=h, json={"lost": True})
    other = auth(login(c, "u2")["token"])
    assert c.patch(f"/api/character/spells/{ids[0]}", headers=other, json={"lost": False}).status_code == 404
    assert c.patch("/api/character/spells/99999", headers=h, json={"lost": True}).status_code == 404
    r = c.post("/api/character/spells/rest", headers=h)
    assert r.status_code == 200 and [k["lost"] for k in r.json()["spells"]] == [False, False]


# ---------- XP ----------


def test_xp_api(api):
    c, h = _bob_client(api)  # Bob is level 2: next level at 20 XP
    sheet = c.get("/api/character", headers=h).json()
    assert (sheet["xp"], sheet["xp_needed"]) == (0, 20)
    r = c.post("/api/character/xp", headers=h, json={"add": 7})
    assert r.status_code == 200 and r.json()["xp"] == 7
    r = c.post("/api/character/xp", headers=h, json={"add": 20})
    assert r.json()["xp"] == 27 and r.json()["level"] == 2  # never auto-levels
    r = c.post("/api/character/xp", headers=h, json={"add": -10})
    assert r.json()["xp"] == 17
    assert c.post("/api/character/xp", headers=h, json={"add": -18}).status_code == 400
    assert c.get("/api/character", headers=h).json()["xp"] == 17
    # the stats form sets it outright; omitting it leaves it alone
    stats = {"scores": [15, 14, 10, 16, 10, 10], "level": 3, "max_hp": 8, "armor_class": 11}
    r = c.patch("/api/character/stats", headers=h, json={**stats, "xp": 4})
    assert (r.json()["xp"], r.json()["xp_needed"]) == (4, 30)
    assert c.patch("/api/character/stats", headers=h, json=stats).json()["xp"] == 4
    assert c.patch("/api/character/stats", headers=h, json={**stats, "xp": -1}).status_code == 400
    # visible, but not writable, from the party view
    other = auth(login(c, "u2")["token"])
    assert c.get("/api/characters/u1", headers=other).json()["xp"] == 4
