"""creatures get a title, tags, a kind and a type link; encounters become free text

Revision ID: 0023_creature_tags_kinds
Revises: 0022_borrow_name
Create Date: 2026-11-06

`creatures` gets plain ADD COLUMNs on purpose: it is referenced by
`creature_encounters` and a batch (table rebuild) fails on SQLite with
foreign_keys=ON once rows exist (see 0022). `type_id` has no database foreign
key for the same reason; the service keeps it valid.

`creature_encounters` is referenced by nothing, so it is rebuilt in a batch:
`date`, `session`, `place` and `outcome` fold into one `details` text and
`created_at` takes the old date so the newest-first order holds.
"""
from datetime import datetime
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0023_creature_tags_kinds"
down_revision: Union[str, None] = "0022_borrow_name"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("creatures", sa.Column("title", sa.String(), nullable=True))
    op.add_column("creatures", sa.Column("tags", sa.Text(), nullable=True))
    op.add_column(
        "creatures",
        sa.Column("kind", sa.String(), nullable=False, server_default="type"),
    )
    op.add_column("creatures", sa.Column("type_id", sa.Integer(), nullable=True))

    with op.batch_alter_table("creature_encounters") as batch:
        batch.add_column(sa.Column("details", sa.Text(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, date, session, place, outcome FROM creature_encounters")
    ).all()
    for row_id, day, session, place, outcome in rows:
        header = " · ".join(
            part.strip() for part in (str(day or ""), session or "", place or "")
            if part and part.strip()
        )
        details = header + (("\n" + outcome.strip()) if outcome and outcome.strip() else "")
        created = f"{day} 00:00:00" if day else None
        bind.execute(
            sa.text(
                "UPDATE creature_encounters SET details = :d, "
                "created_at = COALESCE(:c, created_at) WHERE id = :i"
            ),
            {"d": details or None, "c": created, "i": row_id},
        )

    with op.batch_alter_table("creature_encounters") as batch:
        batch.drop_column("date")
        batch.drop_column("session")
        batch.drop_column("place")
        batch.drop_column("outcome")


def downgrade() -> None:
    with op.batch_alter_table("creature_encounters") as batch:
        batch.add_column(sa.Column("date", sa.Date(), nullable=True))
        batch.add_column(sa.Column("session", sa.String(), nullable=True))
        batch.add_column(sa.Column("place", sa.String(), nullable=True))
        batch.add_column(sa.Column("outcome", sa.Text(), nullable=True))

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, details, created_at FROM creature_encounters")
    ).all()
    for row_id, details, created in rows:
        day = str(created or datetime.now())[:10]
        bind.execute(
            sa.text("UPDATE creature_encounters SET date = :d, outcome = :o WHERE id = :i"),
            {"d": day, "o": details, "i": row_id},
        )

    with op.batch_alter_table("creature_encounters") as batch:
        batch.alter_column("date", existing_type=sa.Date(), nullable=False)
        batch.drop_column("details")

    for column in ("type_id", "kind", "tags", "title"):
        op.drop_column("creatures", column)
