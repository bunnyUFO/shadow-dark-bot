"""Applying library effects to a sheet: copy semantics, the template reference,
and which effects are suggested first."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from shadowdark_bot.db import session_scope
from shadowdark_bot.dev_seed import BRANNOC_ID, YSOLDE_ID, seed_sample_data
from shadowdark_bot.models import CharacterEffect, EffectTemplate
from shadowdark_bot.services import characters, effects
from shadowdark_bot.spell_data import seed_spells
from shadowdark_bot.web.server import build_local_app

ASSETS = Path(__file__).resolve().parents[1] / "src/shadowdark_bot/web/static/assets"


@pytest.fixture
def client(dbsession):
    seed_spells()
    with session_scope() as s:
        assert seed_sample_data(s) is True
    c = TestClient(build_local_app(), client=("127.0.0.1", 50000))
    token = c.post("/api/auth/local", json={"user_id": YSOLDE_ID}).json()["token"]
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def test_applied_effect_is_an_independent_copy(client):
    mage = next(e for e in client.get("/api/effects").json() if e["name"] == "Mage Armor")
    # the client prefills from the template, and may tweak before adding
    r = client.post("/api/character/effects", json={
        "name": "Mage Armor (strong)", "max_rounds": 12, "bonuses": {"ac": "=15"},
        "template_id": mage["id"],
    })
    assert r.status_code == 200, r.text
    eff = r.json()["effects"][-1]
    assert (eff["name"], eff["max_rounds"], eff["bonuses"], eff["template_id"]) == (
        "Mage Armor (strong)", 12, {"ac": "=15"}, mage["id"])
    assert r.json()["effective"]["ac"]["total"] == 15

    # editing the sheet effect leaves the template alone, and vice versa
    client.patch(f"/api/character/effects/{eff['id']}", json={"bonuses": {"ac": "=16"}})
    client.patch(f"/api/effects/{mage['id']}", json={"max_rounds": 3})
    again = client.get("/api/effects/%d" % mage["id"]).json()
    assert (again["bonuses"], again["max_rounds"]) == ({"ac": "=14"}, 3)
    mine = next(e for e in client.get("/api/character").json()["effects"] if e["id"] == eff["id"])
    assert (mine["bonuses"], mine["max_rounds"]) == ({"ac": "=16"}, 12)

    # custom effects carry no reference; an unknown template id is dropped
    r = client.post("/api/character/effects", json={"name": "Torch", "max_rounds": 6})
    assert r.json()["effects"][-1]["template_id"] is None
    r = client.post("/api/character/effects", json={"name": "Odd", "template_id": 99999})
    assert r.status_code == 200 and r.json()["effects"][-1]["template_id"] is None

    # deleting the template keeps the sheet copy, clearing only the reference
    assert client.delete(f"/api/effects/{mage['id']}").status_code == 200
    mine = next(e for e in client.get("/api/character").json()["effects"] if e["id"] == eff["id"])
    assert mine["name"] == "Mage Armor (strong)" and mine["template_id"] is None


def test_apply_from_library_service(dbsession):
    with session_scope() as s:
        characters.save_identity(s, "u1", name="Bob")
        t = effects.create_template(s, "u1", name="Bless", max_rounds=4, bonuses={"str": "+1"})
        e = characters.add_effect(s, "u1", t.name, t.max_rounds, t.bonus_map(), template_id=t.id)
        assert e.template_id == t.id
        t.max_rounds = 9
        s.flush()
        assert e.max_rounds == 4
        effects.delete_template(s, t.id)
        s.expire_all()
        assert s.get(CharacterEffect, e.id).template_id is None
        assert s.query(EffectTemplate).count() == 0


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_suggested_ids_rule():
    script = (
        f"import {{ suggestedIds }} from {json.dumps((ASSETS / 'suggest.js').as_uri())};"
        "const templates = ["
        " {id: 1, links: [{kind: 'spell', target_id: 10}]},"
        " {id: 2, links: [{kind: 'item', target_id: 20}]},"
        " {id: 3, links: [{kind: 'creature', target_id: 10}]},"
        " {id: 4, links: [{kind: 'spell', target_id: 11}, {kind: 'item', target_id: 21}]},"
        " {id: 5, links: []}];"
        "const sheet = {spells: [{spell: {id: 10}}, {spell: null}], held: {items: [{item_id: 20}, {item_id: null}]}};"
        "console.log(JSON.stringify([...suggestedIds(templates, sheet)]));"
    )
    res = subprocess.run(["node", "--input-type=module", "-e", script],
                         capture_output=True, text=True, check=True)
    # spell 10 and item 20 match; a creature link with the same id does not
    assert json.loads(res.stdout) == [1, 2]
