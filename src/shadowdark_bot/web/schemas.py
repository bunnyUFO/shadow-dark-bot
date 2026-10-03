"""JSON shapes the web API returns, and builders from the ORM rows.

Derived numbers (ability modifiers, AC including DEX, spellcasting total, slot
usage) are computed here with the same rules helpers the Discord embeds use,
so the web sheet and the bot always agree.
"""

from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.orm import Session

from shadowdark_bot import storage
from shadowdark_bot.currency import format_cp
from shadowdark_bot.models import InventoryEntry, Item, Location, PlayerCharacter, Spell
from shadowdark_bot.rules import ABILITIES, ability_modifier, spellcasting_modifier
from shadowdark_bot.services import characters


class UserOut(BaseModel):
    id: str
    display_name: str
    avatar_url: str | None = None


class AbilityOut(BaseModel):
    key: str
    label: str
    score: int
    modifier: int


class SpellcastingOut(BaseModel):
    ability: str
    ability_modifier: int
    talent_bonus: int
    total: int


class SpellOut(BaseModel):
    id: int
    name: str
    tier: int
    classes: list[str]
    alignment: str | None
    duration: str | None
    range: str | None
    description: str | None


class KnownSpellOut(BaseModel):
    id: int  # character_spells row id (what forget will take)
    name: str
    tier: int | None
    spell: SpellOut | None  # reference text, when linked to the built-in list


class ItemStackOut(BaseModel):
    id: int
    name: str
    quantity: int
    source: str  # "catalog" | "freeform"
    item_id: int | None
    item_type: str | None
    gear_slots_each: float
    bundle_size: int
    slot_cost: float
    value_cp: int | None
    description: str | None
    notes: str | None


class StoreOut(BaseModel):
    location_id: int
    name: str
    used_slots: float
    max_slots: float
    items: list[ItemStackOut]


class CharacterSheetOut(BaseModel):
    user_id: str
    name: str
    char_class: str | None
    title: str | None
    ancestry: str | None
    alignment: str | None
    background: str | None
    level: int
    max_hp: int | None
    armor_class_base: int | None
    armor_class: int | None  # base + DEX modifier, as the bot shows it
    abilities: list[AbilityOut]
    spellcasting: SpellcastingOut | None
    proficiencies: str | None
    spells: list[KnownSpellOut]
    gold_cp: int
    gold: str
    held: StoreOut
    stash: StoreOut | None  # omitted on another player's sheet
    languages: str | None
    talents: str | None
    additional_info: str | None
    updated_at: datetime


class CatalogItemOut(BaseModel):
    id: int
    name: str
    item_type: str
    gear_slots: float
    bundle_size: int
    value_cp: int | None
    description: str | None


# ---------- builders ----------


def spell_out(sp: Spell) -> SpellOut:
    return SpellOut(
        id=sp.id,
        name=sp.name,
        tier=sp.tier,
        classes=sp.class_list,
        alignment=sp.alignment,
        duration=sp.duration,
        range=sp.range_,
        description=sp.description,
    )


def catalog_item_out(item: Item) -> CatalogItemOut:
    return CatalogItemOut(
        id=item.id,
        name=item.name,
        item_type=item.item_type,
        gear_slots=item.gear_slots,
        bundle_size=item.bundle_size,
        value_cp=item.value_cp,
        description=item.description,
    )


def _stack_out(e: InventoryEntry) -> ItemStackOut:
    item = e.item if e.is_catalog else None
    return ItemStackOut(
        id=e.id,
        name=e.display_name,
        quantity=e.quantity,
        source="catalog" if e.is_catalog else "freeform",
        item_id=e.item_id,
        item_type=item.item_type if item else None,
        gear_slots_each=e.effective_gear_slots,
        bundle_size=e.effective_bundle_size,
        slot_cost=e.slot_cost,
        value_cp=item.value_cp if item else None,
        # Freeform stacks keep their typed description in `notes`.
        description=item.description if item else e.notes,
        notes=e.notes if item else None,
    )


def _store_out(session: Session, loc: Location) -> StoreOut:
    entries = storage.location_entries(session, loc.id)
    return StoreOut(
        location_id=loc.id,
        name=loc.name,
        used_slots=storage.used_slots(entries),
        max_slots=loc.max_gear_slots,
        items=[_stack_out(e) for e in entries],
    )


def character_sheet(
    session: Session, char: PlayerCharacter, *, include_stash: bool
) -> CharacterSheetOut:
    """The full sheet. `include_stash=False` for another player's read-only view
    (the stash is private, matching `/character show`)."""
    held, stash = storage.ensure_character_locations(session, char)

    spellcasting = None
    if char.spell_ability is not None:
        score = getattr(char, f"{char.spell_ability}_score")
        spellcasting = SpellcastingOut(
            ability=char.spell_ability,
            ability_modifier=ability_modifier(score),
            talent_bonus=char.spell_check_bonus,
            total=spellcasting_modifier(score, char.spell_check_bonus),
        )

    armor_class = None
    if char.armor_class is not None:
        armor_class = char.armor_class + ability_modifier(char.dex_score)

    return CharacterSheetOut(
        user_id=char.user_id,
        name=char.name,
        char_class=char.char_class,
        title=char.title,
        ancestry=char.ancestry,
        alignment=char.alignment,
        background=char.background,
        level=char.level,
        max_hp=char.max_hp,
        armor_class_base=char.armor_class,
        armor_class=armor_class,
        abilities=[
            AbilityOut(
                key=key,
                label=label,
                score=getattr(char, f"{key}_score"),
                modifier=ability_modifier(getattr(char, f"{key}_score")),
            )
            for key, label in ABILITIES
        ],
        spellcasting=spellcasting,
        proficiencies=char.proficiencies,
        spells=[
            KnownSpellOut(
                id=cs.id,
                name=cs.display_name,
                tier=cs.display_tier,
                spell=spell_out(cs.spell) if cs.spell is not None else None,
            )
            for cs in characters.sorted_spells(char)
        ],
        gold_cp=char.gold_cp,
        gold=format_cp(char.gold_cp) or "0cp",
        held=_store_out(session, held),
        stash=_store_out(session, stash) if include_stash else None,
        languages=char.languages,
        talents=char.talents,
        additional_info=char.additional_info,
        updated_at=char.updated_at,
    )
