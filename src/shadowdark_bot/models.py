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

from shadowdark_bot.rules import stack_slots

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


class PlayerCharacter(Base):
    """One Shadow Dark character per Discord user (keyed by user_id).

    Gold is stored as integer copper (like items/coffers). Current HP is not
    tracked — only max HP. `spell_ability` names the casting stat (int/wis) or
    is NULL for non-casters; `spell_check_bonus` captures talent-granted bonuses.
    """

    __tablename__ = "player_characters"
    __table_args__ = (
        CheckConstraint("level >= 1", name="ck_characters_level_positive"),
        CheckConstraint(
            "spell_ability IN ('int', 'wis')", name="ck_characters_spell_ability"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    char_class: Mapped[str | None] = mapped_column(String)
    level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    max_hp: Mapped[int | None] = mapped_column(Integer)
    armor_class: Mapped[int | None] = mapped_column(Integer)
    str_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    dex_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    con_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    int_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    wis_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    cha_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=10, server_default="10"
    )
    gold_cp: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    talents: Mapped[str | None] = mapped_column(Text)
    spell_ability: Mapped[str | None] = mapped_column(String)
    spell_check_bonus: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    items: Mapped[list["CharacterItem"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )
    spells: Mapped[list["CharacterSpell"]] = relationship(
        back_populates="character", cascade="all, delete-orphan"
    )


class CharacterItem(Base):
    """A stack of items a character is carrying.

    Hybrid: `item_id` links to the shared catalog (reusing its gear_slots /
    bundle_size), or is NULL for a freeform typed item whose `name`,
    `slots_each`, and `bundle_size` live on this row.
    """

    __tablename__ = "character_items"
    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_character_items_quantity_positive"),
        # One stack per catalog item. Freeform rows (item_id NULL) are exempt —
        # SQLite treats NULLs as distinct — and are de-duped by name in code.
        UniqueConstraint("character_id", "item_id", name="uq_character_item"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("player_characters.id"), nullable=False
    )
    item_id: Mapped[int | None] = mapped_column(ForeignKey("items.id"))
    name: Mapped[str | None] = mapped_column(String)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    slots_each: Mapped[float | None] = mapped_column(Float)
    bundle_size: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    character: Mapped[PlayerCharacter] = relationship(back_populates="items")
    item: Mapped[Item | None] = relationship()

    @property
    def is_catalog(self) -> bool:
        return self.item_id is not None

    @property
    def display_name(self) -> str:
        if self.item_id is not None and self.item is not None:
            return self.item.name
        return self.name or "(unnamed)"

    @property
    def effective_gear_slots(self) -> float:
        if self.item_id is not None and self.item is not None:
            return self.item.gear_slots
        return self.slots_each if self.slots_each is not None else 1.0

    @property
    def effective_bundle_size(self) -> int:
        if self.item_id is not None and self.item is not None:
            return self.item.bundle_size
        return self.bundle_size if self.bundle_size is not None else 1

    @property
    def slot_cost(self) -> float:
        """Bundle-aware gear-slot cost of this stack."""
        return stack_slots(
            self.quantity, self.effective_gear_slots, self.effective_bundle_size
        )


class Spell(Base):
    """Built-in spell reference (seeded from the Shadow Dark Player Quickstart).

    `classes` is a comma-joined subset of {'priest', 'wizard'} (some spells,
    like Light, belong to both).
    """

    __tablename__ = "spells"
    __table_args__ = (CheckConstraint("tier >= 1", name="ck_spells_tier_positive"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    tier: Mapped[int] = mapped_column(Integer, nullable=False)
    classes: Mapped[str] = mapped_column(String, nullable=False)
    duration: Mapped[str | None] = mapped_column(String)
    range_: Mapped[str | None] = mapped_column("range", String)
    description: Mapped[str | None] = mapped_column(Text)

    @property
    def class_list(self) -> list[str]:
        return [c for c in self.classes.split(",") if c]


class CharacterSpell(Base):
    """A spell a character knows.

    Hybrid: `spell_id` links to the built-in `spells` reference (so the detail
    view can show duration/range/description), or is NULL for a freeform spell
    whose `name`/`tier` live on this row (e.g. higher-tier spells not in the
    quickstart).
    """

    __tablename__ = "character_spells"
    __table_args__ = (
        UniqueConstraint("character_id", "spell_id", name="uq_character_spell"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    character_id: Mapped[int] = mapped_column(
        ForeignKey("player_characters.id"), nullable=False
    )
    spell_id: Mapped[int | None] = mapped_column(ForeignKey("spells.id"))
    name: Mapped[str | None] = mapped_column(String)
    tier: Mapped[int | None] = mapped_column(Integer)
    added_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )

    character: Mapped[PlayerCharacter] = relationship(back_populates="spells")
    spell: Mapped[Spell | None] = relationship()

    @property
    def is_reference(self) -> bool:
        return self.spell_id is not None

    @property
    def display_name(self) -> str:
        if self.spell_id is not None and self.spell is not None:
            return self.spell.name
        return self.name or "(unnamed)"

    @property
    def display_tier(self) -> int | None:
        if self.spell_id is not None and self.spell is not None:
            return self.spell.tier
        return self.tier
