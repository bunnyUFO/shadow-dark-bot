"""unify character inventory onto locations

Gives every character two owned locations (held + stash), extends
inventory_entries to hold freeform stacks (like the old character_items), moves
existing character_items into each character's held location, and drops the
character_items table. Location names are no longer globally unique — only
guild (unowned) locations are, via a partial unique index.

Revision ID: 0015_character_locations
Revises: 0014_character_title
Create Date: 2026-07-03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_character_locations"
down_revision: Union[str, None] = "0014_character_title"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Names the otherwise-unnamed inline UNIQUE(name) so batch mode can drop it.
_NAMING = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    # `locations` is referenced by inventory_entries/treasury_entries, and the
    # bot enables PRAGMA foreign_keys. Batch table-recreation does DROP TABLE
    # locations, which fails the FK check when child rows exist (and, being
    # non-transactional, leaves an _alembic_tmp_locations table behind on a
    # failed run). Turn FKs off for the rebuild and clear any leftover temp
    # tables so a previously-failed run can recover.
    op.execute("PRAGMA foreign_keys=OFF")
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_locations")
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_inventory_entries")

    # 1. locations: add ownership, drop the global unique on name, add a role
    #    check, then a partial unique index scoped to guild (unowned) rows.
    with op.batch_alter_table(
        "locations", recreate="always", naming_convention=_NAMING
    ) as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.String()))
        batch_op.add_column(sa.Column("role", sa.String()))
        batch_op.drop_constraint("uq_locations_name", type_="unique")
        batch_op.create_check_constraint(
            "ck_locations_role", "role IN ('held', 'stash')"
        )
    op.create_index(
        "uq_locations_guild_name",
        "locations",
        ["name"],
        unique=True,
        sqlite_where=sa.text("owner_user_id IS NULL"),
    )

    # 2. inventory_entries: become the unified stack model (catalog OR freeform).
    with op.batch_alter_table("inventory_entries", recreate="always") as batch_op:
        batch_op.add_column(sa.Column("name", sa.String()))
        batch_op.add_column(sa.Column("slots_each", sa.Float()))
        batch_op.add_column(sa.Column("bundle_size", sa.Integer()))
        batch_op.alter_column("item_id", existing_type=sa.Integer(), nullable=True)

    # 3. Backfill: two locations per character, and move their carried items into
    #    the held location.
    conn = op.get_bind()
    meta = sa.MetaData()
    locations = sa.Table("locations", meta, autoload_with=conn)
    inv = sa.Table("inventory_entries", meta, autoload_with=conn)
    chars = sa.Table("player_characters", meta, autoload_with=conn)
    citems = sa.Table("character_items", meta, autoload_with=conn)

    rows = conn.execute(
        sa.select(chars.c.id, chars.c.user_id, chars.c.name, chars.c.str_score)
    ).all()
    for char_id, user_id, name, str_score in rows:
        held_cap = float(max(10, str_score or 10))
        held_id = conn.execute(
            locations.insert().values(
                name=name,
                kind="inventory",
                description=f"Currently held items for {name}",
                max_gear_slots=held_cap,
                owner_user_id=user_id,
                role="held",
            )
        ).inserted_primary_key[0]
        conn.execute(
            locations.insert().values(
                name=f"{name} Guild Stash",
                kind="inventory",
                description=f"Stored items for {name}",
                max_gear_slots=10.0,
                owner_user_id=user_id,
                role="stash",
            )
        )
        carried = conn.execute(
            sa.select(citems).where(citems.c.character_id == char_id)
        ).all()
        for ci in carried:
            conn.execute(
                inv.insert().values(
                    location_id=held_id,
                    item_id=ci.item_id,
                    name=ci.name,
                    quantity=ci.quantity,
                    slots_each=ci.slots_each,
                    bundle_size=ci.bundle_size,
                    notes=ci.notes,
                    added_at=ci.added_at,
                )
            )

    op.drop_table("character_items")


def downgrade() -> None:
    # Same FK caveat as upgrade(): the table rebuilds below drop referenced
    # tables, so disable FKs and clear any leftover temp tables first.
    op.execute("PRAGMA foreign_keys=OFF")
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_locations")
    op.execute("DROP TABLE IF EXISTS _alembic_tmp_inventory_entries")

    # Recreate character_items and move each held location's stacks back.
    op.create_table(
        "character_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "character_id",
            sa.Integer,
            sa.ForeignKey("player_characters.id"),
            nullable=False,
        ),
        sa.Column("item_id", sa.Integer, sa.ForeignKey("items.id")),
        sa.Column("name", sa.String),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("slots_each", sa.Float),
        sa.Column("bundle_size", sa.Integer),
        sa.Column("notes", sa.Text),
        sa.Column(
            "added_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("quantity > 0", name="ck_character_items_quantity_positive"),
        sa.UniqueConstraint("character_id", "item_id", name="uq_character_item"),
    )

    conn = op.get_bind()
    meta = sa.MetaData()
    locations = sa.Table("locations", meta, autoload_with=conn)
    inv = sa.Table("inventory_entries", meta, autoload_with=conn)
    chars = sa.Table("player_characters", meta, autoload_with=conn)
    citems = sa.Table("character_items", meta, autoload_with=conn)

    char_by_user = {
        user_id: char_id
        for char_id, user_id in conn.execute(
            sa.select(chars.c.id, chars.c.user_id)
        ).all()
    }
    held = conn.execute(
        sa.select(locations.c.id, locations.c.owner_user_id).where(
            locations.c.role == "held"
        )
    ).all()
    for loc_id, owner in held:
        char_id = char_by_user.get(owner)
        if char_id is None:
            continue
        for entry in conn.execute(
            sa.select(inv).where(inv.c.location_id == loc_id)
        ).all():
            conn.execute(
                citems.insert().values(
                    character_id=char_id,
                    item_id=entry.item_id,
                    name=entry.name,
                    quantity=entry.quantity,
                    slots_each=entry.slots_each,
                    bundle_size=entry.bundle_size,
                    notes=entry.notes,
                    added_at=entry.added_at,
                )
            )
    # Delete character-owned locations (their entries first, for the FK).
    owned = conn.execute(
        sa.select(locations.c.id).where(locations.c.owner_user_id.isnot(None))
    ).all()
    for (loc_id,) in owned:
        conn.execute(inv.delete().where(inv.c.location_id == loc_id))
        conn.execute(locations.delete().where(locations.c.id == loc_id))

    op.drop_index("uq_locations_guild_name", table_name="locations")
    with op.batch_alter_table(
        "inventory_entries", recreate="always"
    ) as batch_op:
        batch_op.alter_column("item_id", existing_type=sa.Integer(), nullable=False)
        batch_op.drop_column("bundle_size")
        batch_op.drop_column("slots_each")
        batch_op.drop_column("name")
    with op.batch_alter_table(
        "locations", recreate="always", naming_convention=_NAMING
    ) as batch_op:
        batch_op.drop_constraint("ck_locations_role", type_="check")
        batch_op.drop_column("role")
        batch_op.drop_column("owner_user_id")
        batch_op.create_unique_constraint("uq_locations_name", ["name"])
