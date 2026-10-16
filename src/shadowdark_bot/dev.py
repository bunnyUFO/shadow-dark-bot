"""Standalone local web sheet on a seeded dev database; no Discord, no .env.

    python -m shadowdark_bot.dev --reset

Only stdlib is imported at the top: `shadowdark_bot.config` builds its settings
at import time and `db` creates its engine then, so the environment has to be
set before any other `shadowdark_bot` module is imported.
"""

import argparse
import logging
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEV_DB = REPO_ROOT / "data" / "dev.db"

log = logging.getLogger("shadowdark_bot.dev")


def dev_database_url() -> str:
    return "sqlite:///" + DEV_DB.as_posix()


def _delete_dev_db() -> None:
    for suffix in ("", "-wal", "-shm"):
        path = DEV_DB.with_name(DEV_DB.name + suffix)
        try:
            path.unlink(missing_ok=True)
        except PermissionError:
            raise SystemExit(
                "dev.db is in use — stop the running dev server first."
            ) from None


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python -m shadowdark_bot.dev",
        description="Run the local (no-login) web sheet on a seeded dev database.",
    )
    parser.add_argument(
        "--reset", action="store_true", help="delete data/dev.db and reseed"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8081)
    parser.add_argument(
        "--dm",
        action="append",
        default=[],
        metavar="USER_ID",
        help="make this user a Dungeon Master (repeatable), e.g. 900000000000000001",
    )
    args = parser.parse_args()

    # Unconditional: must beat .env and the shell, and a real token never loads.
    os.environ["DATABASE_URL"] = dev_database_url()
    os.environ["DISCORD_TOKEN"] = "dev-runner-no-discord"

    if args.reset:
        _delete_dev_db()

    import uvicorn

    from shadowdark_bot.db import session_scope
    from shadowdark_bot.dev_seed import seed_sample_data
    from shadowdark_bot.main import run_migrations, seed_reference_data
    from shadowdark_bot.web.server import build_local_app

    run_migrations()
    seed_reference_data()
    with session_scope() as session:
        seeded = seed_sample_data(session)
    if seeded:
        log.info("Seeded sample party: Brannoc Ashfall, Ysolde Vane, Brother Aldric")
    else:
        log.info("Dev DB already has characters; left as is.")

    log.info(
        "Dev sheet (no login): http://localhost:%s  — DB: %s", args.port, DEV_DB
    )
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        log.info(
            "Listening on %s; the private-network check still refuses public "
            "and proxied requests.",
            args.host,
        )
    if args.dm:
        log.info("Dungeon Masters: %s", ", ".join(args.dm))
    uvicorn.run(
        build_local_app(frozenset(args.dm)),
        host=args.host,
        port=args.port,
        proxy_headers=False,
        log_config=None,
    )


if __name__ == "__main__":
    main()
