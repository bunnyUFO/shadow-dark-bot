"""Applying a library effect to a character: yourself or another party member
(the one add-only write allowed on someone else's sheet)."""

import pytest
from fastapi.testclient import TestClient

from shadowdark_bot.db import session_scope
from shadowdark_bot.dev_seed import ALDRIC_ID, BRANNOC_ID, YSOLDE_ID, seed_sample_data
from shadowdark_bot.services import characters, effects
from shadowdark_bot.services.characters import CharacterNotFound
from shadowdark_bot.spell_data import seed_spells
from shadowdark_bot.web.server import build_local_app


@pytest.fixture
def seeded(dbsession):
    seed_spells()
    with session_scope() as s:
        assert seed_sample_data(s) is True


def _client(user_id):
    c = TestClient(build_local_app(), client=("127.0.0.1", 50000))
    token = c.post("/api/auth/local", json={"user_id": user_id}).json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.fixture
def client(seeded):
    return _client(YSOLDE_ID)


def _template(client, name="Mage Armor"):
    return next(e for e in client.get("/api/effects").json() if e["name"] == name)


def test_service_copies_template_to_target(seeded):
    with session_scope() as s:
        tid = effects.create_template(
            s, "x", name="Bless Test", max_rounds=4, bonuses={"ac": "+1", "str": "+2"}
        ).id
    with session_scope() as s:
        char, eff = effects.apply_template(s, tid, ALDRIC_ID)
        assert char.user_id == ALDRIC_ID
        assert (eff.name, eff.max_rounds, eff.template_id) == ("Bless Test", 4, tid)
        assert eff.bonus_map() == {"ac": "+1", "str": "+2"}
    with session_scope() as s:
        mine = characters.load_character(s, ALDRIC_ID)
        assert any(e.template_id == tid for e in mine.effects)
        # copy: editing the template later leaves the copy alone
        effects.update_template(s, tid, max_rounds=9)
    with session_scope() as s:
        copy = next(e for e in characters.load_character(s, ALDRIC_ID).effects if e.template_id == tid)
        assert copy.max_rounds == 4


def test_service_unknown_target_or_template(seeded):
    with session_scope() as s:
        tid = effects.create_template(s, "x", name="Z", max_rounds=1).id
    with session_scope() as s:
        with pytest.raises(CharacterNotFound):
            effects.apply_template(s, tid, "nobody")
        with pytest.raises(CharacterNotFound):
            effects.apply_template(s, 99999, ALDRIC_ID)


def test_api_apply_to_me_defaults(client):
    mage = _template(client)
    r = client.post(f"/api/effects/{mage['id']}/apply", json={})
    assert r.status_code == 200
    assert r.json()["target_user_id"] == YSOLDE_ID
    assert r.json()["effect_name"] == "Mage Armor"
    mine = client.get("/api/character").json()["effects"]
    added = [e for e in mine if e["name"] == "Mage Armor"]
    assert len(added) == 1 and added[0]["template_id"] == mage["id"]
    new = added[0]
    assert new["template_id"] == mage["id"]
    assert new["max_rounds"] == mage["max_rounds"]
    assert new["bonuses"] == mage["bonuses"]


def test_api_apply_to_another_character(client):
    mage = _template(client)
    r = client.post(f"/api/effects/{mage['id']}/apply", json={"target_user_id": BRANNOC_ID})
    assert r.status_code == 200
    body = r.json()
    assert body["target_user_id"] == BRANNOC_ID and body["target_name"]
    theirs = _client(BRANNOC_ID).get("/api/character").json()["effects"]
    assert any(e["template_id"] == mage["id"] for e in theirs)
    # my own sheet is unchanged
    mine = client.get("/api/character").json()["effects"]
    assert all(e["template_id"] != mage["id"] for e in mine)


def test_api_apply_404s(client):
    mage = _template(client)
    assert client.post(f"/api/effects/{mage['id']}/apply", json={"target_user_id": "nobody"}).status_code == 404
    assert client.post("/api/effects/99999/apply", json={}).status_code == 404


def test_api_apply_needs_login(seeded):
    c = TestClient(build_local_app(), client=("127.0.0.1", 50000))
    assert c.post("/api/effects/1/apply", json={}).status_code == 401
