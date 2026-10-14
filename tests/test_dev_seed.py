"""The dev runner's sample data and the runner's import hygiene."""

import os
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from shadowdark_bot import dev, storage
from shadowdark_bot.db import session_scope
from shadowdark_bot.dev_seed import (
    ALDRIC_ID,
    BRANNOC_ID,
    YSOLDE_ID,
    seed_sample_data,
)
from shadowdark_bot.models import (
    Borrow,
    Coffer,
    InventoryEntry,
    Item,
    Location,
    PlayerCharacter,
    TreasuryEntry,
)
from shadowdark_bot.services import characters, creatures
from shadowdark_bot.services import creatures as creatures_service
from shadowdark_bot.services.characters import CharacterError
from shadowdark_bot.spell_data import seed_spells
from shadowdark_bot.web.app import create_app
from shadowdark_bot.web.auth import SessionSigner


@pytest.fixture
def seeded(dbsession):
    seed_spells()
    with session_scope() as s:
        assert seed_sample_data(s) is True


def _used(session, user_id, role):
    loc = storage.character_location(session, user_id, role)
    return loc, storage.used_slots(storage.location_entries(session, loc.id))


def test_seed_content(seeded):
    with session_scope() as s:
        chars = s.scalars(select(PlayerCharacter).order_by(PlayerCharacter.user_id)).all()
        assert [(c.user_id, c.name) for c in chars] == [
            (BRANNOC_ID, "Brannoc Ashfall"),
            (YSOLDE_ID, "Ysolde Vane"),
            (ALDRIC_ID, "Brother Aldric"),
        ]
        for c in chars:
            assert storage.held_location(s, c.user_id) is not None
            assert storage.stash_location(s, c.user_id) is not None

        for loc in s.scalars(select(Location).where(Location.kind == "inventory")):
            used = storage.used_slots(storage.location_entries(s, loc.id))
            assert used <= loc.max_gear_slots, loc.name

        _, ysolde_used = _used(s, YSOLDE_ID, "held")
        held, brannoc_used = _used(s, BRANNOC_ID, "held")
        assert (ysolde_used, brannoc_used, held.max_gear_slots) == (8.0, 12.0, 16.0)
        assert _used(s, BRANNOC_ID, "stash")[1] == 2.0
        assert _used(s, ALDRIC_ID, "held")[1] == 3.0

        assert s.scalar(select(Coffer.balance_cp)) == 15240
        guild = s.scalars(
            select(Location).where(
                Location.kind == "inventory", Location.owner_user_id.is_(None)
            )
        ).all()
        assert {g.name: storage.used_slots(storage.location_entries(s, g.id)) for g in guild} == {
            "Guild Hall Storeroom": 18.0,
            "Pack Mule": 4.0,
        }
        entries = s.scalars(select(TreasuryEntry)).all()
        assert len(entries) == 3
        borrowed = [e for e in entries if e.status == "borrowed"]
        assert len(borrowed) == 1
        borrow = s.scalars(
            select(Borrow).where(Borrow.treasury_entry_id == borrowed[0].id)
        ).one()
        assert borrow.borrower_id == BRANNOC_ID and borrow.returned_at is None
        ring = [
            e.display_name
            for e in storage.location_entries(s, held.id)
            if e.item_id == borrowed[0].item_id
        ]
        assert ring == ["Ring of Feather Falling"]


def test_seed_creatures(seeded):
    with session_scope() as s:
        rows = {c.name: (c, n, last) for c, n, last in creatures.list_creatures(s)}
        assert list(rows) == ["Cave crawler", "Goblin", "Pale thing in the well"]
        goblin, n, last = rows["Goblin"]
        assert n == 2 and last is not None
        assert all(
            getattr(goblin, f) is not None
            for f in (*creatures_service.INT_FIELDS, "attacks", "movement", "alignment")
        )
        crawler = rows["Cave crawler"][0]
        assert crawler.ac == 13 and crawler.hp is None and crawler.str_mod is None
        unknown = rows["Pale thing in the well"][0]
        assert unknown.level is None and unknown.ac is None and unknown.hp is None


def _counts(s):
    return [
        s.scalar(select(func.count()).select_from(m))
        for m in (PlayerCharacter, Item, Location, InventoryEntry, TreasuryEntry, Borrow)
    ]


def test_seed_is_idempotent(seeded):
    with session_scope() as s:
        before = _counts(s)
        assert seed_sample_data(s) is False
        assert _counts(s) == before


def test_seed_through_the_local_app(seeded):
    app = create_app(
        mode="local",
        signer=SessionSigner("x" * 40, 60, purpose="local"),
        static_dir=None,
    )
    client = TestClient(app, client=("127.0.0.1", 50000))
    picks = client.get("/api/auth/local/characters").json()
    assert [c["name"] for c in picks] == [
        "Brannoc Ashfall",
        "Brother Aldric",
        "Ysolde Vane",
    ]
    spell_counts = {}
    for pick in picks:
        token = client.post("/api/auth/local", json={"user_id": pick["user_id"]}).json()[
            "token"
        ]
        resp = client.get("/api/character", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        spell_counts[pick["name"]] = len(resp.json()["spells"])
    assert spell_counts == {"Brannoc Ashfall": 0, "Brother Aldric": 3, "Ysolde Vane": 6}

    guild = client.get("/api/guild", headers={"Authorization": f"Bearer {token}"}).json()
    assert guild["coffers"]["balance_cp"] == 15240
    borrowers = [t["borrower"]["name"] for t in guild["treasury"] if t["borrower"]]
    assert borrowers == ["Brannoc Ashfall"]


def test_ready_for_give_capacity_error(seeded):
    with session_scope() as s:
        held = storage.held_location(s, BRANNOC_ID)
        ysolde_held = storage.held_location(s, YSOLDE_ID)
        torch = next(
            e
            for e in storage.location_entries(s, held.id)
            if e.display_name == "Torch"
        )

        def give(qty):
            return characters.give(
                s, BRANNOC_ID, role="held", entry_id=torch.id,
                target_location_id=ysolde_held.id, quantity=qty,
            )

        with pytest.raises(CharacterError):
            give(3)
        give(1)
        assert _used(s, YSOLDE_ID, "held")[1] == 9.0


def test_runner_is_import_clean():
    env = {k: v for k, v in os.environ.items() if k not in ("DISCORD_TOKEN", "DATABASE_URL")}
    code = (
        "import sys, shadowdark_bot.dev; "
        "assert 'shadowdark_bot.config' not in sys.modules"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    assert dev.dev_database_url().endswith("/data/dev.db")
