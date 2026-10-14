"""Run the real migrations over a database that already holds data.

Fresh-database tests can't catch a migration that works on empty tables but
fails once rows exist: SQLite with foreign_keys=ON refuses the DROP TABLE in a
batch (table-rebuild) migration when other tables reference its rows. This
stops at the previous head, fills in realistic rows, then upgrades to head.
"""

import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from shadowdark_bot.config import settings

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS_HEAD = "0021_character_effect_template"


def _insert(conn: sqlite3.Connection, table: str, **values) -> int:
    """Insert a row, filling any other NOT NULL column that has no default."""
    for _cid, name, ctype, notnull, default, pk in conn.execute(f"PRAGMA table_info({table})"):
        if name in values or pk or not notnull or default is not None:
            continue
        values[name] = 0 if "INT" in (ctype or "").upper() else "x"
    cols = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    cur = conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(values.values()))
    return cur.lastrowid


@pytest.fixture
def alembic_cfg(tmp_path, monkeypatch):
    db_path = tmp_path / "bot.db"
    url = f"sqlite:///{db_path.as_posix()}"
    monkeypatch.setattr(settings, "DATABASE_URL", url)  # migrations/env.py reads this
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg, db_path


def test_upgrade_to_head_with_existing_rows(alembic_cfg):
    cfg, db_path = alembic_cfg
    command.upgrade(cfg, PREVIOUS_HEAD)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    _insert(conn, "player_characters", user_id="u1", name="Brannoc")
    item = _insert(conn, "items", name="Ring of Feather Falling", item_type="magical")
    held = _insert(conn, "locations", name="Held", kind="inventory", owner_user_id="u1",
                   max_gear_slots=10)
    _insert(conn, "inventory_entries", location_id=held, item_id=item, quantity=1)
    vault = _insert(conn, "locations", name="Treasury", kind="treasury", max_gear_slots=0)
    entry = _insert(conn, "treasury_entries", location_id=vault, item_id=item, status="borrowed")
    _insert(conn, "borrows", treasury_entry_id=entry, borrower_id="u1")
    _insert(conn, "borrows", treasury_entry_id=entry, borrower_id="ghost",
            returned_at="2026-01-01 00:00:00")
    conn.commit()
    conn.close()

    command.upgrade(cfg, "head")  # must not raise

    conn = sqlite3.connect(db_path)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(borrows)")]
        assert "borrower_name" in cols
        rows = conn.execute(
            "SELECT borrower_id, borrower_name FROM borrows ORDER BY id"
        ).fetchall()
        assert rows == [("u1", None), ("ghost", None)]
        assert conn.execute("SELECT count(*) FROM player_characters").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM inventory_entries").fetchone()[0] == 1
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
