from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


ITEM_TYPE_COMMON = "common"
ITEM_TYPE_MAGICAL = "magical"
ITEM_TYPE_CRAFTED = "crafted"
ITEM_TYPE_SCROLL = "scroll"
ITEM_TYPE_POTION = "potion"
ITEM_TYPE_WEAPON = "weapon"
ITEM_TYPE_ARMOR = "armor"
ITEM_TYPE_LOOT = "loot"
ITEM_TYPES: tuple[str, ...] = (
    ITEM_TYPE_COMMON,
    ITEM_TYPE_MAGICAL,
    ITEM_TYPE_CRAFTED,
    ITEM_TYPE_SCROLL,
    ITEM_TYPE_POTION,
    ITEM_TYPE_WEAPON,
    ITEM_TYPE_ARMOR,
    ITEM_TYPE_LOOT,
)


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"
    __table_args__ = (
        CheckConstraint("bundle_size >= 1", name="ck_items_bundle_size_positive"),
        CheckConstraint(
            "item_type IN ('common', 'magical', 'crafted', 'scroll', 'potion', "
            "'weapon', 'armor', 'loot')",
            name="ck_items_item_type",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    gear_slots: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    bundle_size: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    value_cp: Mapped[int | None] = mapped_column(Integer)
    item_type: Mapped[str] = mapped_column(
        String, nullable=False, default=ITEM_TYPE_COMMON, server_default=ITEM_TYPE_COMMON
    )
    created_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (
        CheckConstraint("kind IN ('inventory', 'treasury')", name="ck_locations_kind"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    max_gear_slots: Mapped[float] = mapped_column(
        Float, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )


class InventoryEntry(Base):
    __tablename__ = "inventory_entries"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_inventory_quantity_positive"),
        UniqueConstraint("location_id", "item_id", name="uq_inventory_location_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=False)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    added_by: Mapped[str | None] = mapped_column(String)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    location: Mapped[Location] = relationship()
    item: Mapped[Item] = relationship()


class TreasuryEntry(Base):
    __tablename__ = "treasury_entries"
    __table_args__ = (
        CheckConstraint("status IN ('available', 'borrowed')", name="ck_treasury_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"), nullable=False)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id"), nullable=False)
    tag: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String, nullable=False, default="available")
    added_by: Mapped[str | None] = mapped_column(String)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    location: Mapped[Location] = relationship()
    item: Mapped[Item] = relationship()


class Borrow(Base):
    __tablename__ = "borrows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    treasury_entry_id: Mapped[int] = mapped_column(
        ForeignKey("treasury_entries.id"), nullable=False
    )
    borrower_id: Mapped[str] = mapped_column(String, nullable=False)
    borrowed_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    returned_at: Mapped[datetime | None] = mapped_column(DateTime)
    notes: Mapped[str | None] = mapped_column(Text)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    target_kind: Mapped[str | None] = mapped_column(String)
    target_id: Mapped[int | None] = mapped_column(Integer)
    payload_json: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )


class Coffer(Base):
    """Singleton row tracking the guild's shared coffer balance in copper pieces."""

    __tablename__ = "coffers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    balance_cp: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
