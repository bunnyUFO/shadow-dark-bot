"""initial schema

Revision ID: 0001_initial
Revises:
Create Date: 2026-05-20

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("description", sa.Text),
        sa.Column("gear_slots", sa.Float),
        sa.Column("value_gp", sa.Float),
        sa.Column("is_magical", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column("created_by", sa.String),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )

    op.create_table(
        "locations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.String, nullable=False, unique=True),
        sa.Column("kind", sa.String, nullable=False),
        sa.Column("description", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("kind IN ('inventory', 'treasury')", name="ck_locations_kind"),
    )

    op.create_table(
        "inventory_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("location_id", sa.Integer, sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("item_id", sa.Integer, sa.ForeignKey("items.id"), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("notes", sa.Text),
        sa.Column("added_by", sa.String),
        sa.Column(
            "added_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint("quantity > 0", name="ck_inventory_quantity_positive"),
        sa.UniqueConstraint("location_id", "item_id", name="uq_inventory_location_item"),
    )

    op.create_table(
        "treasury_entries",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("location_id", sa.Integer, sa.ForeignKey("locations.id"), nullable=False),
        sa.Column("item_id", sa.Integer, sa.ForeignKey("items.id"), nullable=False),
        sa.Column("tag", sa.Text),
        sa.Column("status", sa.String, nullable=False, server_default="available"),
        sa.Column("added_by", sa.String),
        sa.Column(
            "added_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.CheckConstraint(
            "status IN ('available', 'borrowed')", name="ck_treasury_status"
        ),
    )

    op.create_table(
        "borrows",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column(
            "treasury_entry_id", sa.Integer, sa.ForeignKey("treasury_entries.id"), nullable=False
        ),
        sa.Column("borrower_id", sa.String, nullable=False),
        sa.Column(
            "borrowed_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
        sa.Column("returned_at", sa.DateTime),
        sa.Column("notes", sa.Text),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("actor_id", sa.String, nullable=False),
        sa.Column("action", sa.String, nullable=False),
        sa.Column("target_kind", sa.String),
        sa.Column("target_id", sa.Integer),
        sa.Column("payload_json", sa.Text),
        sa.Column(
            "created_at",
            sa.DateTime,
            nullable=False,
            server_default=sa.func.current_timestamp(),
        ),
    )


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("borrows")
    op.drop_table("treasury_entries")
    op.drop_table("inventory_entries")
    op.drop_table("locations")
    op.drop_table("items")
