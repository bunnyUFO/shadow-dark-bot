"""The creature bestiary: service rules, then the web API on top."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import Creature, CreatureEncounter
from shadowdark_bot.services import creatures
from shadowdark_bot.services.creatures import CreatureError, CreatureNotFound
from shadowdark_bot.web.app import DM_ONLY_LIBRARY, create_app
from shadowdark_bot.web.auth import SessionSigner

# ---------- services ----------


def test_create_with_only_a_name_leaves_everything_unknown(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="  Pale thing in the well ")
        assert c.name == "Pale thing in the well"
        for field in (*creatures.INT_FIELDS, *creatures.TEXT_FIELDS, "alignment"):
            assert getattr(c, field) is None, field
        assert c.created_by == "u1"


def test_blank_inputs_become_unknown(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(
            s, "u1", name="Goblin", level="", ac=" ", hp=None, attacks="  ",
            alignment="", str_mod="",
        )
        assert (c.level, c.ac, c.hp, c.attacks, c.alignment, c.str_mod) == (None,) * 6


def test_partial_update_and_back_to_unknown(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="Goblin", ac=11, hp=5, alignment="c")
        assert c.alignment == "C"
        creatures.update_creature(s, c.id, ac="13")
        assert (c.ac, c.hp, c.name) == (13, 5, "Goblin")  # hp untouched
        creatures.update_creature(s, c.id, ac=None, hp="")
        assert (c.ac, c.hp) == (None, None)
        creatures.update_creature(s, c.id, alignment=None)
        assert c.alignment is None


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("level", -1), ("level", 31), ("ac", 0), ("ac", 31), ("hp", -1),
        ("str_mod", -6), ("cha_mod", 11), ("hp", "lots"), ("ac", True),
        ("alignment", "X"),
    ],
)
def test_out_of_range_values_are_rejected(dbsession, field, value):
    with session_scope() as s:
        with pytest.raises(CreatureError):
            creatures.create_creature(s, "u1", name="Goblin", **{field: value})
        assert s.scalar(select(func.count()).select_from(Creature)) == 0


def test_range_edges_are_allowed(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(
            s, "u1", name="Tarrasque", level=30, ac=30, hp=9999,
            str_mod=10, dex_mod=-5,
        )
        assert (c.level, c.hp, c.str_mod, c.dex_mod) == (30, 9999, 10, -5)
        creatures.update_creature(s, c.id, level=0)
        assert c.level == 0


def test_name_is_required_and_cannot_be_blanked(dbsession):
    with session_scope() as s:
        with pytest.raises(CreatureError):
            creatures.create_creature(s, "u1")
        with pytest.raises(CreatureError):
            creatures.create_creature(s, "u1", name="   ")
        c = creatures.create_creature(s, "u1", name="Goblin")
        with pytest.raises(CreatureError):
            creatures.update_creature(s, c.id, name="")
        with pytest.raises(CreatureError):
            creatures.update_creature(s, c.id, nonsense=1)


def test_missing_creature(dbsession):
    with session_scope() as s:
        with pytest.raises(CreatureNotFound):
            creatures.get_creature(s, 99)
        with pytest.raises(CreatureNotFound):
            creatures.update_creature(s, 99, ac=3)
        with pytest.raises(CreatureNotFound):
            creatures.add_encounter(s, "u1", 99, {"details": "x"})


def test_list_search_is_case_insensitive_and_literal(dbsession):
    with session_scope() as s:
        for name in ("Goblin", "Hobgoblin", "Giant 100% rat", "Wolf"):
            creatures.create_creature(s, "u1", name=name)
        names = lambda q: [c.name for c, _ in creatures.list_creatures(s, q)]  # noqa: E731
        assert names("") == ["Giant 100% rat", "Goblin", "Hobgoblin", "Wolf"]
        assert names("GOB") == ["Goblin", "Hobgoblin"]
        assert names("100%") == ["Giant 100% rat"]
        assert names("%") == ["Giant 100% rat"]  # not a wildcard
        assert names("dragon") == []


def test_encounters_are_free_text_with_ownership(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="Goblin")
        other = creatures.create_creature(s, "u1", name="Wolf")
        e = creatures.add_encounter(s, "u2", c.id, {"details": " Session 12, drove them off "})
        assert e.details == "Session 12, drove them off" and e.created_by == "u2"
        creatures.update_encounter(s, c.id, e.id, {"details": "Fled"})
        assert e.details == "Fled"
        with pytest.raises(CreatureError):
            creatures.update_encounter(s, c.id, e.id, {"details": " "})
        with pytest.raises(CreatureError):
            creatures.update_encounter(s, c.id, e.id, {"date": "2026-01-02"})
        with pytest.raises(CreatureError, match="Write what happened"):
            creatures.add_encounter(s, "u1", c.id, {"details": ""})
        with pytest.raises(CreatureNotFound):  # wrong creature
            creatures.update_encounter(s, other.id, e.id, {"details": "x"})
        with pytest.raises(CreatureNotFound):
            creatures.delete_encounter(s, other.id, e.id)
        creatures.delete_encounter(s, c.id, e.id)
        assert s.scalar(select(func.count()).select_from(CreatureEncounter)) == 0


def test_list_has_encounter_count(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="Goblin")
        creatures.create_creature(s, "u1", name="Wolf")
        creatures.add_encounter(s, "u1", c.id, {"details": "one"})
        creatures.add_encounter(s, "u1", c.id, {"details": "two"})
        rows = {x.name: n for x, n in creatures.list_creatures(s)}
        assert rows == {"Goblin": 2, "Wolf": 0}


def test_tags_are_normalised(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="Goblin", tags=" Enemy, enemy ,Ally")
        assert c.tag_list() == ["enemy", "ally"]
        creatures.update_creature(s, c.id, tags=["  Big   Boss ", "", "big boss", "Cave"])
        assert c.tag_list() == ["big boss", "cave"]
        creatures.update_creature(s, c.id, tags=[])
        assert c.tags is None and c.tag_list() == []
        with pytest.raises(CreatureError):
            creatures.update_creature(s, c.id, tags=["x" * 31])
        with pytest.raises(CreatureError):
            creatures.update_creature(s, c.id, tags=[f"t{i}" for i in range(13)])
        creatures.update_creature(s, c.id, tags=[f"t{i}" for i in range(12)])
        with pytest.raises(CreatureError):
            creatures.update_creature(s, c.id, tags=["a,b"])


def test_tag_suggestions_list_defaults_then_used(dbsession):
    with session_scope() as s:
        creatures.create_creature(s, "u1", name="A", tags=["zebra", "enemy", "apple"])
        assert creatures.tag_suggestions(s) == [
            "ally", "enemy", "neutral", "creature", "townfolk", "apple", "zebra",
        ]


def test_kind_and_title(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="Goblin", title="  Boss ")
        assert (c.kind, c.title) == ("type", "Boss")
        creatures.update_creature(s, c.id, kind="NAMED")
        assert c.kind == "named"
        with pytest.raises(CreatureError, match="Kind must be Named or Type"):
            creatures.update_creature(s, c.id, kind="beast")
        with pytest.raises(CreatureError):
            creatures.update_creature(s, c.id, title="x" * 101)


def test_type_link_rules(dbsession):
    with session_scope() as s:
        goblin = creatures.create_creature(s, "u1", name="Goblin")
        grik = creatures.create_creature(s, "u1", name="Grik", kind="named", type_id=goblin.id)
        assert grik.type_id == goblin.id  # kind and type_id together
        other = creatures.create_creature(s, "u1", name="Rex", kind="named")
        with pytest.raises(CreatureError, match="Only a Type can be linked"):
            creatures.update_creature(s, other.id, type_id=grik.id)
        with pytest.raises(CreatureError, match="Only a Type can be linked"):
            creatures.update_creature(s, other.id, type_id=other.id)
        with pytest.raises(CreatureError, match="isn't in the bestiary"):
            creatures.update_creature(s, other.id, type_id=999)
        with pytest.raises(CreatureError, match="Only a Named creature"):
            creatures.update_creature(s, goblin.id, type_id=goblin.id)
        wolf = creatures.create_creature(s, "u1", name="Wolf")
        with pytest.raises(CreatureError, match="Only a Named creature"):
            creatures.update_creature(s, wolf.id, type_id=goblin.id)
        with pytest.raises(CreatureError, match="1 named creature uses Goblin"):
            creatures.update_creature(s, goblin.id, kind="named")
        creatures.update_creature(s, grik.id, type_id="")
        assert grik.type_id is None
        creatures.update_creature(s, grik.id, type_id=goblin.id)
        creatures.update_creature(s, grik.id, kind="type")  # clears its own link
        assert grik.type_id is None


def test_deleting_a_type_unlinks_its_named(dbsession):
    with session_scope() as s:
        goblin = creatures.create_creature(s, "u1", name="Goblin")
        grik = creatures.create_creature(s, "u1", name="Grik", kind="named", type_id=goblin.id)
        creatures.delete_creature(s, goblin.id)
        s.expire_all()
        assert creatures.get_creature(s, grik.id).type_id is None


def test_inherited_only_fills_blank_fields(dbsession):
    with session_scope() as s:
        goblin = creatures.create_creature(
            s, "u1", name="Goblin", ac=11, hp=5, attacks="Club", description="Small")
        grik = creatures.create_creature(
            s, "u1", name="Grik", kind="named", type_id=goblin.id, hp=9)
        assert creatures.inherited(s, grik) == {"ac": 11, "attacks": "Club"}
        assert creatures.inherited(s, goblin) == {}
        loner = creatures.create_creature(s, "u1", name="Rex", kind="named")
        assert creatures.inherited(s, loner) == {}


def test_delete_cascades_to_encounters(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="Goblin")
        creatures.add_encounter(s, "u1", c.id, {"details": "met"})
        assert creatures.delete_creature(s, c.id) == "Goblin"
        s.expire_all()
        assert s.scalar(select(func.count()).select_from(CreatureEncounter)) == 0
        with pytest.raises(CreatureNotFound):
            creatures.get_creature(s, c.id)


# ---------- web API ----------


def _web(user_id, name, *, is_dm):
    signer = SessionSigner("x" * 40, 3600, purpose="local")
    app = create_app(mode="local", signer=signer, static_dir=None)
    tc = TestClient(app, client=("127.0.0.1", 50000))
    tc.headers["Authorization"] = f"Bearer {signer.issue(user_id, name, is_dm=is_dm)}"
    return tc


@pytest.fixture
def client(dbsession):
    """A Dungeon Master: the only kind of user who may change the bestiary."""
    return _web("u1", "Tester", is_dm=True)


@pytest.fixture
def player(dbsession):
    return _web("u2", "Player", is_dm=False)


def test_requires_login(client):
    client.headers.pop("Authorization")
    assert client.get("/api/creatures").status_code == 401
    assert client.post("/api/creatures", json={"name": "x"}).status_code == 401


def test_create_get_and_list_unknowns(client):
    r = client.post("/api/creatures", json={"name": "Pale thing in the well"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "Pale thing in the well"
    assert body["created_by"] == "u1" and body["encounters"] == []
    assert all(body[k] is None for k in ("level", "ac", "hp", "str_mod", "alignment", "notes"))
    assert client.get(f"/api/creatures/{body['id']}").json() == body
    rows = client.get("/api/creatures").json()
    assert body["kind"] == "type" and body["tags"] == [] and body["type"] is None
    assert body["title"] is None and body["inherited"] == {}
    assert rows == [{
        "id": body["id"], "name": "Pale thing in the well", "title": None, "kind": "type",
        "tags": [], "type_id": None, "level": None, "ac": None, "hp": None,
        "alignment": None, "encounter_count": 0,
    }]


def test_patch_partial_and_unknown_again(client):
    cid = client.post("/api/creatures", json={"name": "Goblin", "hp": 5}).json()["id"]
    r = client.patch(f"/api/creatures/{cid}", json={"ac": 11, "str_mod": -1, "alignment": "c"})
    assert r.status_code == 200
    assert (r.json()["ac"], r.json()["hp"], r.json()["str_mod"], r.json()["alignment"]) == (11, 5, -1, "C")
    r = client.patch(f"/api/creatures/{cid}", json={"ac": None, "hp": ""})
    assert (r.json()["ac"], r.json()["hp"], r.json()["str_mod"]) == (None, None, -1)
    assert r.json()["name"] == "Goblin"


def test_validation_errors_are_400_with_detail(client):
    r = client.post("/api/creatures", json={"name": "Goblin", "ac": 99})
    assert r.status_code == 400 and "AC" in r.json()["detail"]
    r = client.post("/api/creatures", json={"ac": 5})
    assert r.status_code == 400 and "name" in r.json()["detail"].lower()
    r = client.post("/api/creatures", json={"name": "G", "hp": "lots"})
    assert r.status_code == 400
    cid = client.post("/api/creatures", json={"name": "Goblin"}).json()["id"]
    assert client.patch(f"/api/creatures/{cid}", json={"alignment": "Z"}).status_code == 400
    assert client.patch(f"/api/creatures/{cid}", json={"name": ""}).status_code == 400
    assert client.get("/api/creatures/999").status_code == 404
    assert client.patch("/api/creatures/999", json={"ac": 3}).status_code == 404
    assert client.delete("/api/creatures/999").status_code == 404


def test_list_search_and_delete(client):
    ids = {
        n: client.post("/api/creatures", json={"name": n}).json()["id"]
        for n in ("Goblin", "Hobgoblin", "Wolf")
    }
    assert [c["name"] for c in client.get("/api/creatures?q=gob").json()] == ["Goblin", "Hobgoblin"]
    assert client.get("/api/creatures?q=zzz").json() == []
    assert client.delete(f"/api/creatures/{ids['Goblin']}").json() == {"deleted": "Goblin"}
    assert [c["name"] for c in client.get("/api/creatures").json()] == ["Hobgoblin", "Wolf"]


def test_kind_tags_title_and_type_round_trip(client):
    goblin = client.post("/api/creatures", json={"name": "Goblin", "ac": 11, "hp": 5}).json()
    r = client.post("/api/creatures", json={
        "name": "Grik", "title": "Boss", "tags": ["Enemy", "boss"], "kind": "named",
        "type_id": goblin["id"], "hp": 9,
    })
    assert r.status_code == 200, r.text
    grik = r.json()
    assert (grik["title"], grik["kind"], grik["tags"]) == ("Boss", "named", ["enemy", "boss"])
    assert grik["type"] == {"id": goblin["id"], "name": "Goblin"}
    assert grik["ac"] is None and grik["inherited"] == {"ac": 11}
    assert client.get(f"/api/creatures/{grik['id']}").json() == grik

    rows = {c["name"]: c for c in client.get("/api/creatures").json()}
    assert rows["Grik"]["ac"] == 11 and rows["Grik"]["hp"] == 9  # effective values
    assert rows["Grik"]["kind"] == "named" and rows["Grik"]["tags"] == ["enemy", "boss"]
    assert rows["Grik"]["title"] == "Boss" and rows["Grik"]["type_id"] == goblin["id"]

    r = client.patch(f"/api/creatures/{grik['id']}", json={"tags": []})
    assert r.json()["tags"] == []
    r = client.patch(f"/api/creatures/{grik['id']}", json={"kind": "type"})
    assert r.json()["type"] is None
    r = client.patch(f"/api/creatures/{goblin['id']}", json={"kind": "bogus"})
    assert r.status_code == 400 and "Kind" in r.json()["detail"]
    r = client.patch(f"/api/creatures/{goblin['id']}", json={"type_id": grik["id"]})
    assert r.status_code == 400


def test_tags_route_is_not_swallowed_by_the_id_route(client):
    client.post("/api/creatures", json={"name": "A", "tags": ["zebra", "enemy"]})
    r = client.get("/api/creatures/tags")
    assert r.status_code == 200
    assert r.json() == ["ally", "enemy", "neutral", "creature", "townfolk", "zebra"]


def test_encounters_api(client):
    cid = client.post("/api/creatures", json={"name": "Goblin"}).json()["id"]
    base = f"/api/creatures/{cid}/encounters"
    r = client.post(base, json={"details": "Session 3 · Cave\nFled"})
    assert r.status_code == 200, r.text
    r = client.post(base, json={"details": "Back again"})
    encs = r.json()["encounters"]
    assert [e["details"] for e in encs] == ["Back again", "Session 3 · Cave\nFled"]  # newest first
    first = encs[1]
    assert first["created_by"] == "u1" and "created_at" in first

    r = client.patch(f"{base}/{first['id']}", json={"details": "Won"})
    upd = [e for e in r.json()["encounters"] if e["id"] == first["id"]][0]
    assert upd["details"] == "Won"

    listing = client.get("/api/creatures").json()[0]
    assert listing["encounter_count"] == 2 and "last_seen" not in listing

    assert client.post(base, json={"details": "  "}).status_code == 400
    assert client.post(base, json={}).status_code == 400
    assert client.post("/api/creatures/999/encounters", json={"details": "x"}).status_code == 404
    assert client.patch(f"{base}/999", json={"details": "x"}).status_code == 404
    r = client.delete(f"{base}/{first['id']}")
    assert len(r.json()["encounters"]) == 1
    assert client.delete(f"{base}/{first['id']}").status_code == 404

    client.delete(f"/api/creatures/{cid}")
    assert client.get(f"/api/creatures/{cid}").status_code == 404


def test_only_a_dm_changes_the_bestiary(client, player):
    cid = client.post("/api/creatures", json={"name": "Goblin"}).json()["id"]
    base = f"/api/creatures/{cid}"
    eid = client.post(f"{base}/encounters", json={"details": "met"}).json()["encounters"][0]["id"]
    attempts = [
        player.post("/api/creatures", json={"name": "Wolf"}),
        player.patch(base, json={"ac": 3}),
        player.patch(base, json={"tags": ["enemy"], "kind": "named", "type_id": cid}),
        player.delete(base),
        player.post(f"{base}/encounters", json={"details": "x"}),
        player.patch(f"{base}/encounters/{eid}", json={"details": "x"}),
        player.delete(f"{base}/encounters/{eid}"),
    ]
    for r in attempts:
        assert r.status_code == 403, r.text
        assert r.json()["detail"] == DM_ONLY_LIBRARY

    # reading stays open to everyone, and nothing was changed
    assert [c["name"] for c in player.get("/api/creatures").json()] == ["Goblin"]
    assert player.get("/api/creatures/tags").status_code == 200
    got = player.get(base).json()
    assert got["ac"] is None and len(got["encounters"]) == 1
