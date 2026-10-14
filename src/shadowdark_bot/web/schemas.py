"""JSON shapes the web API returns, and builders from the ORM rows.

Derived numbers (ability modifiers, AC including DEX, spellcasting total, slot
usage) are computed here with the same rules helpers the Discord embeds use,
so the web sheet and the bot always agree.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from shadowdark_bot import storage
from shadowdark_bot.currency import format_cp
from shadowdark_bot.models import (
    Borrow,
    Coffer,
    InventoryEntry,
    Item,
    Location,
    PlayerCharacter,
    Spell,
    TreasuryEntry,
)
from shadowdark_bot.rules import (
    ABILITIES,
    ability_modifier,
    spellcasting_modifier,
    xp_needed,
)
from shadowdark_bot.services import characters, guild as guild_service


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
    lost: bool = False  # failed casting check: unavailable until rest


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


class EffectOut(BaseModel):
    id: int
    name: str
    max_rounds: int | None  # None: no duration, lasts until removed
    rounds_used: int
    rounds_left: int | None
    ended: bool
    bonuses: dict[str, str]  # stat -> "+3" | "-1" | "=14"
    template_id: int | None = None  # the library effect it was copied from


class StatOut(BaseModel):
    """One stat after active effects. `base` is the unmodified value (for AC the
    armour plus DEX modifier; for an ability, its modifier); `sources` names the
    active effects that changed it."""

    base: int
    total: int
    boosted: bool
    sources: list[dict[str, str]]  # {"effect": name, "change": "+3"}


class StoreOut(BaseModel):
    location_id: int
    name: str
    description: str | None = None
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
    xp: int
    xp_needed: int  # XP to reach the next level (level x 10)
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
    effects: list[EffectOut] = []
    # ac, hp, str..cha with active effects applied (display only; base fields
    # above stay the stored values)
    effective: dict[str, StatOut] = {}
    updated_at: datetime


class CharacterSummaryOut(BaseModel):
    user_id: str
    name: str
    char_class: str | None
    title: str | None
    ancestry: str | None
    level: int


class CatalogItemOut(BaseModel):
    id: int
    name: str
    item_type: str
    gear_slots: float
    bundle_size: int
    value_cp: int | None
    description: str | None


class BorrowerOut(BaseModel):
    user_id: str  # "" when lent to a free-text name
    # Character name -> Discord display name -> name stored on the loan ->
    # "Unknown player"; never empty.
    name: str
    borrowed_at: datetime
    notes: str | None


class TreasuryEntryOut(BaseModel):
    id: int
    item: CatalogItemOut
    tag: str | None
    status: str  # "available" | "borrowed"
    borrower: BorrowerOut | None


class CoffersOut(BaseModel):
    balance_cp: int
    balance: str


class PartyMemberOut(BaseModel):
    user_id: str
    name: str


class GuildOut(BaseModel):
    coffers: CoffersOut
    party: list[PartyMemberOut]  # every character, for "borrow for…"
    locations: list[StoreOut]  # shared guild storage (not characters' held/stash)
    treasury: list[TreasuryEntryOut]


class GiveTargetOut(BaseModel):
    location_id: int
    name: str
    kind: Literal["guild", "self", "character"]
    owner_user_id: str | None


# ---------- request bodies (edits) ----------


class GoldIn(BaseModel):
    gold: str  # "12gp 5sp 3cp", same grammar as the bot's Gold modal


class BorrowIn(BaseModel):
    # Neither set = borrow for yourself. `for_user_id` = another character's
    # owner; `name` = a free-text borrower (ledger only, no inventory).
    for_user_id: str | None = None
    name: str | None = None


class CarryIn(BaseModel):
    name: str
    quantity: int = 1
    to: Literal["held", "stash"] = "held"
    slots_each: float = 1.0
    notes: str | None = None


class EditStackIn(BaseModel):
    # Omitted/None = leave alone; notes "" clears them. Catalog stacks accept
    # only quantity and notes.
    quantity: int | None = None
    name: str | None = None
    slots_each: float | None = None
    notes: str | None = None


class BuyIn(BaseModel):
    quantity: int = 1
    pay_from: Literal["character", "guild"]
    destination_location_id: int


class GiveIn(BaseModel):
    target_location_id: int
    quantity: int | None = None  # None = the whole stack


class TakeIn(BaseModel):
    quantity: int | None = None  # None = the whole stack
    to: Literal["held", "stash"] = "held"


class LearnSpellIn(BaseModel):
    spell_id: int


class SpellLostIn(BaseModel):
    lost: bool


class EffectIn(BaseModel):
    name: str
    max_rounds: int | None = None  # omit/null: no duration
    bonuses: dict[str, str | None] = {}  # stat -> "+3" | "-1" | "=14"
    template_id: int | None = None  # set when copied from the effects library


class EffectPatchIn(BaseModel):
    """Only the fields sent are changed."""

    name: str | None = None
    max_rounds: int | None = None
    rounds_used: int | None = None
    bonuses: dict[str, str | None] | None = None


class IdentityIn(BaseModel):
    name: str
    char_class: str | None = None
    title: str | None = None
    ancestry: str | None = None
    alignment: str | None = None
    background: str | None = None


class StatsIn(BaseModel):
    scores: list[int]  # STR DEX CON INT WIS CHA
    level: int
    xp: int | None = None  # omitted = leave unchanged
    max_hp: int | None = None
    armor_class: int | None = None  # the base AC (sheet's armor_class_base)
    spell_ability: str | None = None
    spell_check_bonus: int = 0


class XpIn(BaseModel):
    add: int  # may be negative


class SkillsIn(BaseModel):
    languages: str | None = None
    talents: str | None = None
    additional_info: str | None = None


class ProficienciesIn(BaseModel):
    proficiencies: str | None = None


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


def character_summary(char: PlayerCharacter) -> CharacterSummaryOut:
    return CharacterSummaryOut(
        user_id=char.user_id,
        name=char.name,
        char_class=char.char_class,
        title=char.title,
        ancestry=char.ancestry,
        level=char.level,
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
        description=loc.description,
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

    effects = list(char.effects)
    effective: dict[str, StatOut] = {}

    def apply(key: str, base: int) -> StatOut:
        res = characters.resolve_stat(effects, key, base)
        out = StatOut(
            base=res.base,
            total=res.total,
            boosted=bool(res.sources),
            sources=[{"effect": n, "change": c} for n, c in res.sources],
        )
        effective[key] = out
        return out

    for key, _label in ABILITIES:
        apply(key, ability_modifier(getattr(char, f"{key}_score")))
    # AC is armour + DEX modifier; a DEX effect therefore flows into AC unless
    # an effect sets AC outright.
    if armor_class is not None:
        apply("ac", char.armor_class + effective["dex"].total)
        ac = effective["ac"]
        ac.base = armor_class
        if ac.total != ac.base:
            ac.boosted = True
            if not any(s["change"].startswith("=") for s in ac.sources):
                ac.sources = [
                    {"effect": s["effect"], "change": f"{s['change']} DEX"}
                    for s in effective["dex"].sources
                ] + ac.sources
    if char.max_hp is not None:
        apply("hp", char.max_hp)

    return CharacterSheetOut(
        effects=[
            EffectOut(
                id=e.id,
                name=e.name,
                max_rounds=e.max_rounds,
                rounds_used=e.rounds_used,
                rounds_left=None if e.max_rounds is None else e.max_rounds - e.rounds_used,
                ended=e.ended,
                bonuses=e.bonus_map(),
                template_id=e.template_id,
            )
            for e in effects
        ],
        effective=effective,
        user_id=char.user_id,
        name=char.name,
        char_class=char.char_class,
        title=char.title,
        ancestry=char.ancestry,
        alignment=char.alignment,
        background=char.background,
        level=char.level,
        xp=char.xp,
        xp_needed=xp_needed(char.level),
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
                lost=cs.lost,
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


def guild_overview(
    session: Session, resolve_name: guild_service.NameResolver | None = None
) -> GuildOut:
    """Coffers, every shared storage location with its contents, and the magic
    item treasury with who has each borrowed instance."""
    coffer = session.scalar(select(Coffer).limit(1))
    balance = coffer.balance_cp if coffer is not None else 0

    locations = session.scalars(
        select(Location)
        .where(Location.kind == "inventory", Location.owner_user_id.is_(None))
        .order_by(Location.name)
    ).all()

    entries = session.scalars(
        select(TreasuryEntry)
        .options(joinedload(TreasuryEntry.item))
        .join(Item, TreasuryEntry.item_id == Item.id)
        .order_by(Item.name, TreasuryEntry.id)
    ).all()
    open_borrows = {
        b.treasury_entry_id: b
        for b in session.scalars(select(Borrow).where(Borrow.returned_at.is_(None))).all()
    }
    names = {
        uid: name
        for uid, name in session.execute(
            select(PlayerCharacter.user_id, PlayerCharacter.name).where(
                PlayerCharacter.user_id.in_({b.borrower_id for b in open_borrows.values()})
            )
        ).all()
    }
    party = session.execute(
        select(PlayerCharacter.user_id, PlayerCharacter.name).order_by(
            PlayerCharacter.name, PlayerCharacter.user_id
        )
    ).all()

    treasury = []
    for e in entries:
        borrow = open_borrows.get(e.id) if e.status == "borrowed" else None
        treasury.append(
            TreasuryEntryOut(
                id=e.id,
                item=catalog_item_out(e.item),
                tag=e.tag,
                status=e.status,
                borrower=BorrowerOut(
                    user_id=borrow.borrower_id,
                    name=guild_service.borrower_name(
                        borrow, names.get(borrow.borrower_id), resolve_name
                    ),
                    borrowed_at=borrow.borrowed_at,
                    notes=borrow.notes,
                )
                if borrow is not None
                else None,
            )
        )

    return GuildOut(
        party=[PartyMemberOut(user_id=u, name=n) for u, n in party],
        coffers=CoffersOut(balance_cp=balance, balance=format_cp(balance) or "0cp"),
        locations=[_store_out(session, loc) for loc in locations],
        treasury=treasury,
    )
