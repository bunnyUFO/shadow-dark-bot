"""The creature bestiary: service rules, then the web API on top."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import Creature, CreatureEncounter
from shadowdark_bot.services import creatures
from shadowdark_bot.services.creatures import CreatureError, CreatureNotFound
from shadowdark_bot.web.app import create_app
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
            creatures.add_encounter(s, "u1", 99)


def test_list_search_is_case_insensitive_and_literal(dbsession):
    with session_scope() as s:
        for name in ("Goblin", "Hobgoblin", "Giant 100% rat", "Wolf"):
            creatures.create_creature(s, "u1", name=name)
        names = lambda q: [c.name for c, _, _ in creatures.list_creatures(s, q)]  # noqa: E731
        assert names("") == ["Giant 100% rat", "Goblin", "Hobgoblin", "Wolf"]
        assert names("GOB") == ["Goblin", "Hobgoblin"]
        assert names("100%") == ["Giant 100% rat"]
        assert names("%") == ["Giant 100% rat"]  # not a wildcard
        assert names("dragon") == []


def test_encounters_default_date_and_ownership(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="Goblin")
        other = creatures.create_creature(s, "u1", name="Wolf")
        e = creatures.add_encounter(s, "u2", c.id, {"session": " Session 12 ", "date": ""})
        assert e.date == date.today() and e.session == "Session 12"
        assert e.place is None and e.created_by == "u2"
        creatures.update_encounter(s, c.id, e.id, {"place": "Cave", "date": "2026-01-02"})
        assert (e.place, e.date) == ("Cave", date(2026, 1, 2))
        creatures.update_encounter(s, c.id, e.id, {"place": ""})
        assert e.place is None and e.session == "Session 12"
        with pytest.raises(CreatureError):
            creatures.update_encounter(s, c.id, e.id, {"date": "yesterday"})
        with pytest.raises(CreatureNotFound):  # wrong creature
            creatures.update_encounter(s, other.id, e.id, {"place": "x"})
        with pytest.raises(CreatureNotFound):
            creatures.delete_encounter(s, other.id, e.id)
        creatures.delete_encounter(s, c.id, e.id)
        assert s.scalar(select(func.count()).select_from(CreatureEncounter)) == 0


def test_list_has_encounter_count_and_last_seen(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="Goblin")
        creatures.create_creature(s, "u1", name="Wolf")
        creatures.add_encounter(s, "u1", c.id, {"date": "2026-03-01"})
        creatures.add_encounter(s, "u1", c.id, {"date": "2026-05-09"})
        rows = {x.name: (n, last) for x, n, last in creatures.list_creatures(s)}
        assert rows == {"Goblin": (2, date(2026, 5, 9)), "Wolf": (0, None)}


def test_delete_cascades_to_encounters(dbsession):
    with session_scope() as s:
        c = creatures.create_creature(s, "u1", name="Goblin")
        creatures.add_encounter(s, "u1", c.id)
        assert creatures.delete_creature(s, c.id) == "Goblin"
        s.expire_all()
        assert s.scalar(select(func.count()).select_from(CreatureEncounter)) == 0
        with pytest.raises(CreatureNotFound):
            creatures.get_creature(s, c.id)


# ---------- web API ----------


@pytest.fixture
def client(dbsession):
    signer = SessionSigner("x" * 40, 3600, purpose="local")
    app = create_app(mode="local", signer=signer, static_dir=None)
    tc = TestClient(app, client=("127.0.0.1", 50000))
    tc.headers["Authorization"] = f"Bearer {signer.issue('u1', 'Tester')}"
    return tc


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
    assert rows == [{
        "id": body["id"], "name": "Pale thing in the well", "level": None, "ac": None,
        "hp": None, "alignment": None, "encounter_count": 0, "last_seen": None,
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


def test_encounters_api(client):
    cid = client.post("/api/creatures", json={"name": "Goblin"}).json()["id"]
    base = f"/api/creatures/{cid}/encounters"
    r = client.post(base, json={"date": "2026-03-01", "session": "Session 3", "place": "Cave"})
    assert r.status_code == 200, r.text
    r = client.post(base, json={"date": "2026-05-09", "outcome": "Fled"})
    r = client.post(base, json={})  # today
    encs = r.json()["encounters"]
    assert [e["date"] for e in encs] == [date.today().isoformat(), "2026-05-09", "2026-03-01"]
    first = encs[2]
    assert first["session"] == "Session 3" and first["created_by"] == "u1"

    r = client.patch(f"{base}/{first['id']}", json={"outcome": "Won", "place": ""})
    upd = [e for e in r.json()["encounters"] if e["id"] == first["id"]][0]
    assert (upd["outcome"], upd["place"], upd["session"]) == ("Won", None, "Session 3")

    listing = client.get("/api/creatures").json()[0]
    assert listing["encounter_count"] == 3 and listing["last_seen"] == date.today().isoformat()

    assert client.post(base, json={"date": "soon"}).status_code == 400
    assert client.post("/api/creatures/999/encounters", json={}).status_code == 404
    assert client.patch(f"{base}/999", json={"place": "x"}).status_code == 404
    r = client.delete(f"{base}/{first['id']}")
    assert len(r.json()["encounters"]) == 2
    assert client.delete(f"{base}/{first['id']}").status_code == 404

    client.delete(f"/api/creatures/{cid}")
    assert client.get(f"/api/creatures/{cid}").status_code == 404
