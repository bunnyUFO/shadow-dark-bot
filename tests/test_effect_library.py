"""The effects library: templates, links to items/spells/creatures, the API."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from shadowdark_bot.db import session_scope
from shadowdark_bot.dev_seed import seed_sample_data
from shadowdark_bot.models import (
    EffectTemplate,
    EffectTemplateLink,
    Item,
    Spell,
)
from shadowdark_bot.services import creatures, effects
from shadowdark_bot.services.characters import CharacterError
from shadowdark_bot.spell_data import seed_spells
from shadowdark_bot.web.server import build_local_app


@pytest.fixture
def seeded(dbsession):
    seed_spells()
    with session_scope() as s:
        assert seed_sample_data(s) is True


@pytest.fixture
def client(seeded):
    # "local" mode: no Discord, any seeded character can sign in.
    c = TestClient(build_local_app(), client=("127.0.0.1", 50000))
    token = c.post(
        "/api/auth/local", json={"user_id": "900000000000000001"}
    ).json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _ids(session):
    return {
        "spell": session.scalar(select(Spell.id).where(Spell.name == "Light")),
        "item": session.scalar(select(Item.id).where(Item.name == "Torch")),
    }


# ---------- service ----------


def test_template_validation_and_case_insensitive_names(dbsession):
    with session_scope() as s:
        t = effects.create_template(
            s, "u1", name="  Haste ", max_rounds=5, bonuses={"dex": "+2", "ac": ""}
        )
        assert (t.name, t.max_rounds, t.bonus_map()) == ("Haste", 5, {"dex": "+2"})
        with pytest.raises(CharacterError, match="already"):
            effects.create_template(s, "u1", name="haste")
        other = effects.create_template(s, "u1", name="Slow")
        with pytest.raises(CharacterError, match="already"):
            effects.update_template(s, other.id, name="HASTE")
        effects.update_template(s, other.id, name="slow")  # own name, new case: fine
        for bad in (
            dict(name=" "), dict(name="x" * 61), dict(name="x", max_rounds=0),
            dict(name="x", max_rounds=101), dict(name="x", bonuses={"ac": "lots"}),
            dict(name="x", bonuses={"ac": "+11"}), dict(name="x", bonuses={"luck": "+1"}),
            dict(name="x", description="d" * 2001),
        ):
            with pytest.raises(CharacterError):
                effects.create_template(s, "u1", **bad)
        effects.update_template(s, t.id, max_rounds=None, bonuses={})
        assert (t.max_rounds, t.bonuses) == (None, None)


def test_links_validate_target_and_cascade(seeded):
    with session_scope() as s:
        ids = _ids(s)
        goblin = next(c for c, *_ in creatures.list_creatures(s) if c.name == "Goblin")
        t = effects.create_template(s, "u1", name="Glow")
        effects.add_link(s, t.id, "spell", ids["spell"])
        effects.add_link(s, t.id, "spell", ids["spell"])  # idempotent
        effects.add_link(s, t.id, "item", ids["item"])
        effects.add_link(s, t.id, "creature", goblin.id)
        assert len(t.links) == 3
        with pytest.raises(CharacterError):
            effects.add_link(s, t.id, "spell", 999999)
        with pytest.raises(CharacterError):
            effects.add_link(s, t.id, "weapon", 1)
        names = effects.target_names(s, t.links)
        assert names[("spell", ids["spell"])] == "Light"
        # a template can have many sources and a source many templates
        t2 = effects.create_template(s, "u1", name="Lantern light")
        effects.add_link(s, t2.id, "spell", ids["spell"])
        assert [x.name for x in effects.list_templates(s, kind="spell", target_id=ids["spell"])] == ["Glow", "Lantern light"]
        assert [x.name for x in effects.list_templates(s, kind="item")] == ["Giant Strength", "Glow"]
        with pytest.raises(CharacterError):
            effects.list_templates(s, target_id=1)
        # deleting a creature clears its links; deleting a template clears its own
        creatures.delete_creature(s, goblin.id)
        s.expire(t)
        assert len(t.links) == 2
        s.expire_all()
        before = s.scalar(select(func.count()).select_from(EffectTemplateLink))
        effects.delete_template(s, t.id)
        assert s.scalar(select(func.count()).select_from(EffectTemplateLink)) == before - 2


# ---------- API ----------


def test_effects_api_crud_and_links(client):
    r = client.post("/api/effects", json={
        "name": "Bless", "description": "Lucky", "max_rounds": 5, "bonuses": {"str": "+1"}})
    assert r.status_code == 200, r.text
    eff = r.json()
    assert (eff["name"], eff["max_rounds"], eff["bonuses"], eff["links"]) == ("Bless", 5, {"str": "+1"}, [])
    url = f"/api/effects/{eff['id']}"
    assert client.get(url).json()["description"] == "Lucky"

    assert client.post("/api/effects", json={"name": "bless"}).status_code == 400
    assert client.post("/api/effects", json={"name": ""}).status_code == 400
    assert client.post("/api/effects", json={"name": "x", "bonuses": {"ac": "??"}}).status_code == 400
    assert client.get("/api/effects/99999").status_code == 404

    r = client.patch(url, json={"max_rounds": None, "bonuses": {"ac": "=14"}})
    assert (r.json()["max_rounds"], r.json()["bonuses"], r.json()["name"]) == (None, {"ac": "=14"}, "Bless")

    spell_id = client.get("/api/spells").json()[0]["id"]
    item_id = client.get("/api/items?limit=5").json()[0]["id"]
    creature_id = client.get("/api/creatures").json()[0]["id"]
    for kind, tid in (("spell", spell_id), ("item", item_id), ("creature", creature_id)):
        r = client.post(f"{url}/links", json={"kind": kind, "target_id": tid})
        assert r.status_code == 200
    got = {(l["kind"], l["target_id"]): l["target_name"] for l in r.json()["links"]}
    assert set(got) == {("spell", spell_id), ("item", item_id), ("creature", creature_id)}
    assert all(got.values())
    assert client.post(f"{url}/links", json={"kind": "spell", "target_id": 99999}).status_code == 404
    assert client.post(f"{url}/links", json={"kind": "bogus", "target_id": 1}).status_code == 400

    # filters
    assert [e["name"] for e in client.get("/api/effects", params={"kind": "spell", "target_id": spell_id}).json()
            if e["name"] == "Bless"] == ["Bless"]
    assert client.get("/api/effects", params={"q": "zzz"}).json() == []
    assert any(e["id"] == eff["id"] for e in client.get("/api/effects", params={"q": "BLES"}).json())

    r = client.delete(f"{url}/links/spell/{spell_id}")
    assert r.status_code == 200 and len(r.json()["links"]) == 2
    assert client.delete(f"{url}/links/spell/{spell_id}").status_code == 404

    assert client.delete(url).json() == {"deleted": "Bless"}
    assert client.get(url).status_code == 404
    with session_scope() as s:
        assert s.scalar(select(func.count()).select_from(EffectTemplateLink)) == sum(
            len(t.links) for t in s.scalars(select(EffectTemplate)).all())


def test_effects_api_needs_login(seeded):
    c = TestClient(build_local_app(), client=("127.0.0.1", 50000))
    assert c.get("/api/effects").status_code in (401, 403)
    assert c.post("/api/effects", json={"name": "x"}).status_code in (401, 403)


def test_seed_has_linked_effects(client):
    rows = {e["name"]: e for e in client.get("/api/effects").json()}
    assert {"Mage Armor", "Shield of Faith", "Giant Strength", "Frightened"} <= set(rows)
    assert rows["Mage Armor"]["bonuses"] == {"ac": "=14"}
    assert [(l["kind"], l["target_name"]) for l in rows["Mage Armor"]["links"]] == [("spell", "Mage Armor")]
    assert rows["Shield of Faith"]["max_rounds"] == 5
    assert [(l["kind"], l["target_name"]) for l in rows["Frightened"]["links"]] == [("creature", "Goblin")]
    assert rows["Giant Strength"]["links"][0]["kind"] == "item"
