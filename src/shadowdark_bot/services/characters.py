"""Character-sheet operations: loading, editing, carried inventory and spells.

Every write takes the caller's `Session` (the caller owns the transaction, so
use `session_scope()`), validates its input, and raises `CharacterError` with a
user-facing reason on failure — the caller adds its own "**Failed to …**"
header. Raising inside `session_scope()` rolls the transaction back, so a
failed operation never leaves partial writes behind.

Error reasons use Discord-style Markdown (`**bold**`) since that is the wording
players already see in the bot.
"""

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from shadowdark_bot import storage
from shadowdark_bot.currency import format_cp
from shadowdark_bot.models import (
    EFFECT_BONUS_KEYS,
    ITEM_TYPE_MAGICAL,
    LOCATION_ROLE_HELD,
    LOCATION_ROLE_STASH,
    Borrow,
    CharacterEffect,
    CharacterSpell,
    InventoryEntry,
    Item,
    Location,
    PlayerCharacter,
    Spell,
    TreasuryEntry,
)
from shadowdark_bot.rules import ABILITIES, SPELL_ABILITIES, fmt_slots, stack_slots
from shadowdark_bot.services.coffers import get_or_create_coffer

# ---------- Errors ----------


class CharacterError(Exception):
    """A rule or validation failure; `str(err)` is the user-facing reason."""


class CharacterNotFound(CharacterError):
    """The user has no character. Callers usually word this themselves."""

    def __init__(self, message: str = "Character not found.") -> None:
        super().__init__(message)


# ---------- Loading ----------


def load_character(session: Session, user_id: str) -> PlayerCharacter | None:
    """Load a character with its spells (and their reference rows). Carried items
    live in the character's held/stash locations — see the storage helpers."""
    return session.scalars(
        select(PlayerCharacter)
        .options(
            selectinload(PlayerCharacter.spells).joinedload(CharacterSpell.spell),
        )
        .where(PlayerCharacter.user_id == user_id)
    ).first()


def require_character(session: Session, user_id: str) -> PlayerCharacter:
    char = load_character(session, user_id)
    if char is None:
        raise CharacterNotFound()
    return char


def sorted_spells(char: PlayerCharacter) -> list[CharacterSpell]:
    return sorted(
        char.spells, key=lambda s: ((s.display_tier or 0), s.display_name.lower())
    )


def find_known_spell(char: PlayerCharacter, cs_id: int) -> CharacterSpell | None:
    return next((s for s in char.spells if s.id == cs_id), None)


def location_entry(
    session: Session, user_id: str, role: str, entry_id: int
) -> InventoryEntry | None:
    """A stack in one of the user's own locations, or None if it isn't there."""
    loc = storage.character_location(session, user_id, role)
    if loc is None:
        return None
    entry = session.get(InventoryEntry, entry_id)
    if entry is None or entry.location_id != loc.id:
        return None
    return entry


def touch(char: PlayerCharacter) -> None:
    char.updated_at = datetime.now(UTC)


def role_label(role: str) -> str:
    return "held items" if role == LOCATION_ROLE_HELD else "stash"


# ---------- Parsing (shared by the Discord modals and the web API) ----------


ALIGNMENTS = {"lawful": "Lawful", "neutral": "Neutral", "chaotic": "Chaotic"}


def parse_scores(raw: str) -> list[int]:
    """Parse 'STR DEX CON INT WIS CHA' into six ints (space/comma separated)."""
    parts = raw.replace(",", " ").split()
    if len(parts) != 6:
        raise ValueError("Enter exactly six scores, e.g. `14 12 13 10 8 15`.")
    scores: list[int] = []
    for p in parts:
        try:
            n = int(p)
        except ValueError as err:
            raise ValueError(f'"{p}" is not a whole number.') from err
        _check_score(n)
        scores.append(n)
    return scores


def _check_score(n: int) -> None:
    if not 1 <= n <= 30:
        raise ValueError(f"Score {n} is out of range (1–30).")


def _check_scores(scores: Sequence[int]) -> None:
    if len(scores) != 6:
        raise ValueError("Enter exactly six scores, e.g. `14 12 13 10 8 15`.")
    for n in scores:
        _check_score(n)


def parse_optional_int(raw: str, *, minimum: int | None = None) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    n = int(raw)
    if minimum is not None and n < minimum:
        raise ValueError(f"Must be ≥ {minimum}.")
    return n


def parse_spellcasting(raw: str) -> tuple[str | None, int]:
    """Parse the combined spellcasting field into (ability, talent bonus).

    Accepts "INT", "WIS +1", "int-1", "none"/blank. The ability picks the spell
    list; the optional signed number is the talent spell-check bonus."""
    raw = raw.strip()
    if raw.lower() in ("", "none", "-"):
        return None, 0
    match = re.fullmatch(r"([A-Za-z]+)\s*([+-]?\d+)?", raw)
    if match is None:
        raise ValueError("Spellcasting must look like INT, WIS +1, or none.")
    ability = match.group(1).lower()
    if ability not in SPELL_ABILITIES:
        raise ValueError("Spellcasting stat must be INT, WIS, or none.")
    bonus = int(match.group(2)) if match.group(2) else 0
    return ability, bonus


def format_spellcasting(char: PlayerCharacter) -> str:
    if char.spell_ability is None:
        return ""
    text = char.spell_ability.upper()
    if char.spell_check_bonus:
        text += f" {char.spell_check_bonus:+d}"
    return text


def parse_alignment(raw: str | None) -> str | None:
    """Normalize an alignment ("lawful", "Chaotic", "none", blank) to its display
    form or None. Raises ValueError for anything else."""
    value = (raw or "").strip().lower()
    if value in ("", "none", "-"):
        return None
    if value in ALIGNMENTS:
        return ALIGNMENTS[value]
    raise ValueError("Alignment must be Lawful, Neutral, Chaotic, or none.")


def parse_class_title(raw: str) -> tuple[str | None, str | None]:
    """Split the combined "Class & title" field: everything before the first
    comma is the class, the remainder is the title."""
    raw = raw.strip()
    if "," in raw:
        class_part, title_part = raw.split(",", 1)
        return class_part.strip() or None, title_part.strip() or None
    return raw or None, None


def _clean(text: str | None) -> str | None:
    return (text or "").strip() or None


def _clean_languages(text: str | None) -> str | None:
    """Comma list with each language once (case-insensitive); first spelling and order win."""
    seen: dict[str, str] = {}
    for part in (text or "").split(","):
        part = part.strip()
        if part:
            seen.setdefault(part.casefold(), part)
    return ", ".join(seen.values()) or None


# ---------- Sheet edits ----------


def save_identity(
    session: Session,
    user_id: str,
    *,
    name: str,
    char_class: str | None = None,
    title: str | None = None,
    ancestry: str | None = None,
    alignment: str | None = None,
    background: str | None = None,
) -> PlayerCharacter:
    """Create or update the character's identity. This is also character
    creation: a user without a character gets one (name required)."""
    clean_name = name.strip()
    if not clean_name:
        raise CharacterError("Name cannot be empty.")
    try:
        clean_alignment = parse_alignment(alignment)
    except ValueError as err:
        raise CharacterError(str(err)) from err

    char = session.scalar(
        select(PlayerCharacter).where(PlayerCharacter.user_id == user_id)
    )
    if char is None:
        char = PlayerCharacter(user_id=user_id, name=clean_name)
        session.add(char)
    char.name = clean_name
    char.char_class = _clean(char_class)
    char.title = _clean(title)
    char.ancestry = _clean(ancestry)
    char.alignment = clean_alignment
    char.background = _clean(background)
    touch(char)
    session.flush()
    # Create (on first save) or rename this character's storage locations.
    storage.ensure_character_locations(session, char)
    return char


def update_stats(
    session: Session,
    user_id: str,
    *,
    scores: Sequence[int],
    level: int,
    max_hp: int | None,
    armor_class: int | None,
    spell_ability: str | None,
    spell_check_bonus: int = 0,
    xp: int | None = None,
) -> PlayerCharacter:
    """Ability scores (STR DEX CON INT WIS CHA order), level, max HP, base AC,
    and the spellcasting stat + talent bonus. `xp` None leaves XP unchanged."""
    try:
        _check_scores(scores)
    except ValueError as err:
        raise CharacterError(str(err)) from err
    if level < 1:
        raise CharacterError("Level must be a whole number ≥ 1.")
    if max_hp is not None and max_hp < 0:
        raise CharacterError("Max HP must be ≥ 0.")
    if xp is not None and xp < 0:
        raise CharacterError("XP must be ≥ 0.")
    if spell_ability is not None and spell_ability not in SPELL_ABILITIES:
        raise CharacterError("Spellcasting stat must be INT, WIS, or none.")

    char = require_character(session, user_id)
    for (key, _), value in zip(ABILITIES, scores, strict=True):
        setattr(char, f"{key}_score", value)
    char.level = level
    if xp is not None:
        char.xp = xp
    char.max_hp = max_hp
    char.armor_class = armor_class
    char.spell_ability = spell_ability
    char.spell_check_bonus = spell_check_bonus if spell_ability is not None else 0
    touch(char)
    session.flush()
    # STR drives held-inventory capacity — keep the held location in sync.
    storage.ensure_character_locations(session, char)
    return char


def add_xp(session: Session, user_id: str, delta: int) -> PlayerCharacter:
    """Add (or, if negative, remove) XP. The total can't drop below 0; it is
    never converted to a level automatically."""
    char = require_character(session, user_id)
    total = char.xp + delta
    if total < 0:
        raise CharacterError(f"XP can't go below 0 (you have {char.xp}).")
    char.xp = total
    touch(char)
    session.flush()
    return char


def set_gold(session: Session, user_id: str, gold_cp: int | None) -> PlayerCharacter:
    if gold_cp is not None and gold_cp < 0:
        raise CharacterError("Gold can't be negative.")
    char = require_character(session, user_id)
    char.gold_cp = gold_cp if gold_cp is not None else 0
    touch(char)
    session.flush()
    return char


def set_proficiencies(
    session: Session, user_id: str, proficiencies: str | None
) -> PlayerCharacter:
    char = require_character(session, user_id)
    char.proficiencies = _clean(proficiencies)
    touch(char)
    session.flush()
    return char


def set_skills_knowledge(
    session: Session,
    user_id: str,
    *,
    languages: str | None,
    talents: str | None,
    additional_info: str | None,
) -> PlayerCharacter:
    char = require_character(session, user_id)
    char.languages = _clean_languages(languages)
    char.talents = _clean(talents)
    char.additional_info = _clean(additional_info)
    touch(char)
    session.flush()
    return char


@dataclass
class DeletePreview:
    name: str
    stack_count: int


def delete_preview(session: Session, user_id: str) -> DeletePreview:
    """What deleting the character would drop (for the confirmation prompt)."""
    char = require_character(session, user_id)
    count = sum(
        len(storage.location_entries(session, loc.id))
        for loc in (
            storage.held_location(session, user_id),
            storage.stash_location(session, user_id),
        )
        if loc is not None
    )
    return DeletePreview(name=char.name, stack_count=count)


def delete_character(session: Session, user_id: str) -> bool:
    """Delete the character, its held/stash locations and known spells.
    Returns False if there was nothing to delete."""
    char = session.scalar(
        select(PlayerCharacter).where(PlayerCharacter.user_id == user_id)
    )
    if char is None:
        return False
    storage.delete_character_locations(session, user_id)
    session.delete(char)
    return True


# ---------- Carried inventory ----------


def _check_room(
    session: Session,
    loc: Location,
    *,
    current_qty: int,
    add_qty: int,
    gear_slots: float,
    bundle: int,
    where: str,
) -> float:
    """Raise unless `add_qty` more of a stack (already `current_qty` in `loc`)
    fits the location's capacity. Returns the slots used afterwards."""
    delta = stack_slots(current_qty + add_qty, gear_slots, bundle) - stack_slots(
        current_qty, gear_slots, bundle
    )
    used = storage.used_slots(storage.location_entries(session, loc.id))
    cap = loc.max_gear_slots
    new_used = used + delta
    if new_used > cap:
        raise CharacterError(
            f"{where} holds {fmt_slots(used)}/{fmt_slots(cap)} slots; this "
            f"would need {fmt_slots(delta)} more ({fmt_slots(new_used)} total)."
        )
    return new_used


@dataclass
class CarryResult:
    display_name: str
    location_name: str
    used_slots: float
    max_slots: float


def carry(
    session: Session,
    user_id: str,
    *,
    item_name: str,
    quantity: int,
    role: str = LOCATION_ROLE_HELD,
    freeform_slots: float = 1.0,
    notes: str | None = None,
) -> CarryResult:
    """Add items to the character's held items or stash. A name matching the
    catalog links to it; anything else is a freeform stack costing
    `freeform_slots` each. Capacity is bundle-aware."""
    clean_item = item_name.strip()
    if not clean_item:
        raise CharacterError("Item name cannot be empty.")
    if quantity < 1:
        raise CharacterError("Quantity must be ≥ 1.")
    if freeform_slots < 0:
        raise CharacterError("Gear slots each must be ≥ 0.")

    char = require_character(session, user_id)
    held, stash = storage.ensure_character_locations(session, char)
    loc = held if role == LOCATION_ROLE_HELD else stash

    cat_item = session.scalar(select(Item).where(Item.name == clean_item))
    if cat_item is not None:
        existing = storage.find_catalog_stack(session, loc.id, cat_item.id)
        gear_slots, bundle = cat_item.gear_slots, cat_item.bundle_size
    else:
        existing = storage.find_freeform_stack(session, loc.id, clean_item)
        if existing is not None:
            gear_slots = existing.effective_gear_slots
            bundle = existing.effective_bundle_size
        else:
            gear_slots, bundle = freeform_slots, 1

    current_qty = existing.quantity if existing else 0
    new_used = _check_room(
        session,
        loc,
        current_qty=current_qty,
        add_qty=quantity,
        gear_slots=gear_slots,
        bundle=bundle,
        where=f"Your {role_label(role)}",
    )
    cap = loc.max_gear_slots

    stack = storage.add_stack(
        session,
        loc,
        quantity=quantity,
        item=cat_item,
        freeform_name=None if cat_item is not None else clean_item,
        slots_each=freeform_slots,
        bundle_size=1,
        notes=None if cat_item is not None else notes,
    )
    touch(char)
    session.flush()
    return CarryResult(
        display_name=stack.display_name,
        location_name=loc.name,
        used_slots=new_used,
        max_slots=cap,
    )


@dataclass
class AddMoreResult:
    display_name: str
    used_slots: float
    max_slots: float


def add_more(
    session: Session, user_id: str, *, role: str, entry_id: int, quantity: int
) -> AddMoreResult:
    """Add more copies to an existing stack in one of the user's locations."""
    if quantity < 1:
        raise CharacterError("Quantity must be ≥ 1.")
    char = load_character(session, user_id)
    loc = storage.character_location(session, user_id, role)
    entry = location_entry(session, user_id, role, entry_id)
    if loc is None or entry is None:
        raise CharacterError("That item is no longer there.")
    gear_slots, bundle = entry.effective_gear_slots, entry.effective_bundle_size
    delta = stack_slots(entry.quantity + quantity, gear_slots, bundle) - stack_slots(
        entry.quantity, gear_slots, bundle
    )
    used = storage.used_slots(storage.location_entries(session, loc.id))
    cap = loc.max_gear_slots
    new_used = used + delta
    if new_used > cap:
        raise CharacterError(
            f"Your {role_label(role)} holds "
            f"{fmt_slots(used)}/{fmt_slots(cap)} slots; this would need "
            f"{fmt_slots(delta)} more."
        )
    entry.quantity += quantity
    if char is not None:
        touch(char)
    session.flush()
    return AddMoreResult(
        display_name=entry.display_name, used_slots=new_used, max_slots=cap
    )


def return_open_borrows(
    session: Session, user_id: str, item_id: int, limit: int | None = None
) -> list[int]:
    """Close up to `limit` of this user's open treasury borrows of a catalog
    item (oldest first) and mark those entries available again. Returns the
    affected treasury entry ids.

    Used when a borrowed item is removed from a character's inventory — dropping
    it from your pack returns it to the treasury, one borrow per copy removed.
    Inventory isn't adjusted here (the caller has already dropped the copies)."""
    open_borrows = list(
        session.scalars(
            select(Borrow)
            .join(TreasuryEntry, Borrow.treasury_entry_id == TreasuryEntry.id)
            .where(
                Borrow.borrower_id == user_id,
                Borrow.returned_at.is_(None),
                TreasuryEntry.item_id == item_id,
            )
            .order_by(Borrow.borrowed_at)
        ).all()
    )
    if limit is not None:
        open_borrows = open_borrows[:limit]
    now = datetime.now(UTC).replace(tzinfo=None)
    returned: list[int] = []
    for borrow in open_borrows:
        borrow.returned_at = now
        entry = session.get(TreasuryEntry, borrow.treasury_entry_id)
        if entry is not None:
            entry.status = "available"
        returned.append(borrow.treasury_entry_id)
    return returned


@dataclass
class RemoveResult:
    display_name: str
    removed: int
    returned_treasury_ids: list[int] = field(default_factory=list)


def remove(
    session: Session,
    user_id: str,
    *,
    role: str,
    entry_id: int,
    quantity: int | None = None,
) -> RemoveResult:
    """Remove a stack from a character location. `quantity=None` drops the whole
    stack; a value drops that many copies (deleting the stack if it empties).
    Borrowed treasury copies dropped this way are returned to the treasury."""
    if quantity is not None and quantity < 1:
        raise CharacterError("Quantity must be ≥ 1.")
    char = load_character(session, user_id)
    entry = location_entry(session, user_id, role, entry_id)
    if entry is None:
        raise CharacterError("That item is already gone.")
    if quantity is not None and quantity > entry.quantity:
        raise CharacterError(
            f"You only have {entry.quantity}× **{entry.display_name}**."
        )
    display = entry.display_name
    item_id = entry.item_id
    removed = entry.quantity if quantity is None else quantity
    entry.quantity -= removed
    if entry.quantity == 0:
        session.delete(entry)
    # Borrowed treasury copies removed from your pack are returned to the
    # treasury, one per copy dropped. Freeform items have no treasury link.
    returned = (
        return_open_borrows(session, user_id, item_id, limit=removed)
        if item_id is not None
        else []
    )
    if char is not None:
        touch(char)
    session.flush()
    return RemoveResult(
        display_name=display, removed=removed, returned_treasury_ids=returned
    )


# ---------- Give ----------


def give_target_locations(
    session: Session, user_id: str, source: Location
) -> list[Location]:
    """Valid give destinations: guild inventory locations, your own other
    location, and other characters' held locations (not their stashes).
    Guild locations come first, then character locations, each by name."""
    locs = session.scalars(
        select(Location)
        .where(Location.kind == "inventory")
        .order_by(Location.owner_user_id.is_(None).desc(), Location.name)
    ).all()
    return [
        loc
        for loc in locs
        if loc.id != source.id
        # another character's stash is private
        and not (
            loc.owner_user_id is not None
            and loc.owner_user_id != user_id
            and loc.role == LOCATION_ROLE_STASH
        )
    ]


def give_target_error(
    user_id: str, source: Location, target: Location | None, entry: InventoryEntry
) -> str | None:
    """Return why `target` can't receive `entry`, or None if it can."""
    if target is None:
        return "That destination no longer exists."
    if target.kind != "inventory":
        return "You can only give into inventory locations."
    if target.id == source.id:
        return "That's where the item already is."
    if (
        target.owner_user_id is not None
        and target.owner_user_id != user_id
        and target.role == LOCATION_ROLE_STASH
    ):
        return "You can't give into another character's stash."
    if target.owner_user_id is None:
        if not entry.is_catalog:
            return (
                "Freeform items can only be given to another character, not "
                "guild inventory."
            )
        if entry.item is not None and entry.item.item_type == ITEM_TYPE_MAGICAL:
            return (
                "Magical items can't go into guild inventory — they belong in "
                "the treasury."
            )
    return None


def move_stack(
    session: Session, entry: InventoryEntry, target: Location, quantity: int
) -> None:
    """Move `quantity` copies of `entry` into `target`, merging with a matching
    stack there. Capacity-checked (bundle-aware); raises CharacterError when the
    target is full. The caller has validated the destination and the quantity."""
    # Capacity check on the target.
    gear_slots, bundle = entry.effective_gear_slots, entry.effective_bundle_size
    if entry.item_id is not None:
        tstack = storage.find_catalog_stack(session, target.id, entry.item_id)
    else:
        tstack = storage.find_freeform_stack(session, target.id, entry.display_name)
    texisting = tstack.quantity if tstack else 0
    delta = stack_slots(texisting + quantity, gear_slots, bundle) - stack_slots(
        texisting, gear_slots, bundle
    )
    tused = storage.used_slots(storage.location_entries(session, target.id))
    if tused + delta > target.max_gear_slots:
        raise CharacterError(
            f"**{target.name}** holds "
            f"{fmt_slots(tused)}/{fmt_slots(target.max_gear_slots)} slots; "
            f"this would need {fmt_slots(delta)} more."
        )

    # Snapshot the source stack before mutating it.
    src_item = entry.item
    src_name = entry.name
    src_slots = entry.slots_each if entry.slots_each is not None else 1.0
    src_bundle = entry.bundle_size if entry.bundle_size is not None else 1
    src_notes = entry.notes
    entry.quantity -= quantity
    if entry.quantity == 0:
        session.delete(entry)
    storage.add_stack(
        session,
        target,
        quantity=quantity,
        item=src_item,
        freeform_name=None if src_item is not None else src_name,
        slots_each=src_slots,
        bundle_size=src_bundle,
        notes=None if src_item is not None else src_notes,
    )


@dataclass
class GiveResult:
    display_name: str
    quantity: int
    target_name: str


def give(
    session: Session,
    user_id: str,
    *,
    role: str,
    entry_id: int,
    target_location_id: int,
    quantity: int | None = None,
) -> GiveResult:
    """Move a stack (or `quantity` copies of it) from one of the user's locations
    into another character's held items or a storage location, capacity-checked."""
    if quantity is not None and quantity < 1:
        raise CharacterError("Quantity must be ≥ 1.")
    char = load_character(session, user_id)
    source = storage.character_location(session, user_id, role)
    entry = location_entry(session, user_id, role, entry_id)
    if source is None or entry is None:
        raise CharacterError("That item is no longer there.")
    target = session.get(Location, target_location_id)
    error = give_target_error(user_id, source, target, entry)
    if error is not None:
        raise CharacterError(error)
    give_qty = entry.quantity if quantity is None else quantity
    if give_qty > entry.quantity:
        raise CharacterError(
            f"You only have {entry.quantity}× **{entry.display_name}**."
        )

    display = entry.display_name
    move_stack(session, entry, target, give_qty)
    if char is not None:
        touch(char)
    session.flush()
    return GiveResult(display_name=display, quantity=give_qty, target_name=target.name)


# ---------- Edit a stack ----------


@dataclass
class EditStackResult:
    display_name: str
    used_slots: float
    max_slots: float


def edit_stack(
    session: Session,
    user_id: str,
    *,
    role: str,
    entry_id: int,
    quantity: int | None = None,
    name: str | None = None,
    slots_each: float | None = None,
    notes: str | None = None,
) -> EditStackResult:
    """Edit a stack in one of the user's own locations. `None` leaves a field
    alone; `notes=""` clears them. Catalog stacks keep their catalog name and
    slot size (only quantity and notes change); freeform stacks can change
    everything. Growing the stack in slots must fit the location."""
    char = require_character(session, user_id)
    loc = storage.character_location(session, user_id, role)
    entry = location_entry(session, user_id, role, entry_id)
    if loc is None or entry is None:
        raise CharacterError("That item is no longer there.")
    if quantity is not None and quantity < 1:
        raise CharacterError("Quantity must be ≥ 1. Drop the item to remove it.")

    new_name = name.strip() if name is not None else None
    if new_name == "":
        raise CharacterError("Item name cannot be empty.")
    if slots_each is not None and slots_each < 0:
        raise CharacterError("Gear slots each must be ≥ 0.")
    if entry.is_catalog:
        if new_name is not None and new_name != entry.display_name:
            raise CharacterError(
                f"**{entry.display_name}** is a catalog item and can't be renamed."
            )
        if slots_each is not None and slots_each != entry.effective_gear_slots:
            raise CharacterError(
                f"**{entry.display_name}** is a catalog item; its slot size "
                "comes from the catalog."
            )
    elif new_name is not None and new_name.lower() != (entry.name or "").lower():
        clash = storage.find_freeform_stack(session, loc.id, new_name)
        if clash is not None and clash.id != entry.id:
            raise CharacterError(f"You already have an item named **{new_name}** there.")

    gear_slots = entry.effective_gear_slots
    if not entry.is_catalog and slots_each is not None:
        gear_slots = slots_each
    bundle = entry.effective_bundle_size
    new_qty = entry.quantity if quantity is None else quantity
    delta = stack_slots(new_qty, gear_slots, bundle) - entry.slot_cost
    used = storage.used_slots(storage.location_entries(session, loc.id))
    cap = loc.max_gear_slots
    if delta > 0 and used + delta > cap:
        raise CharacterError(
            f"Your {role_label(role)} holds {fmt_slots(used)}/{fmt_slots(cap)} "
            f"slots; this would need {fmt_slots(delta)} more "
            f"({fmt_slots(used + delta)} total)."
        )

    entry.quantity = new_qty
    if not entry.is_catalog:
        if new_name is not None:
            entry.name = new_name
        if slots_each is not None:
            entry.slots_each = slots_each
    if notes is not None:
        entry.notes = notes.strip() or None
    touch(char)
    session.flush()
    return EditStackResult(
        display_name=entry.display_name, used_slots=used + delta, max_slots=cap
    )


# ---------- Buy from the catalog ----------

PAY_FROM = ("character", "guild")


@dataclass
class BuyResult:
    display_name: str
    quantity: int
    total_cp: int
    pay_from: str
    destination_name: str
    balance_cp: int  # what's left in the account that paid


def buy(
    session: Session,
    user_id: str,
    *,
    item_id: int,
    quantity: int,
    pay_from: str,
    destination_location_id: int,
) -> BuyResult:
    """Buy catalog items with the character's gold or the guild coffers and put
    them in the character's own held items/stash or a shared guild storage
    location. Funds and capacity are checked before anything changes."""
    if quantity < 1:
        raise CharacterError("Quantity must be ≥ 1.")
    if pay_from not in PAY_FROM:
        raise CharacterError("Pay from your gold or the guild coffers.")
    char = require_character(session, user_id)
    item = session.get(Item, item_id)
    if item is None:
        raise CharacterError("That item is no longer in the catalog.")
    if item.value_cp is None or item.value_cp <= 0:
        raise CharacterError(f"**{item.name}** has no price set, so it can't be bought.")

    dest = session.get(Location, destination_location_id)
    if dest is None or dest.kind != "inventory":
        raise CharacterError("That destination no longer exists.")
    if dest.owner_user_id is not None and dest.owner_user_id != user_id:
        raise CharacterError("You can only put purchases in your own items or guild storage.")
    if dest.owner_user_id is None and item.item_type == ITEM_TYPE_MAGICAL:
        raise CharacterError(
            "Magical items can't go into guild inventory — they belong in the treasury."
        )

    price = item.value_cp
    total = price * quantity
    if pay_from == "guild":
        account = get_or_create_coffer(session)
        label, balance = "Coffer balance is", account.balance_cp
    else:
        account = char
        label, balance = "You have", char.gold_cp
    if balance < total:
        raise CharacterError(
            f"{label} {_fmt_cp(balance)}; price is {_fmt_cp(total)} "
            f"({quantity} × {_fmt_cp(price)}). Short by {_fmt_cp(total - balance)}."
        )

    existing = storage.find_catalog_stack(session, dest.id, item.id)
    where = (
        f"Your {role_label(dest.role)}" if dest.owner_user_id is not None else dest.name
    )
    _check_room(
        session,
        dest,
        current_qty=existing.quantity if existing else 0,
        add_qty=quantity,
        gear_slots=item.gear_slots,
        bundle=item.bundle_size,
        where=where,
    )

    if pay_from == "guild":
        account.balance_cp -= total
        left = account.balance_cp
    else:
        char.gold_cp -= total
        left = char.gold_cp
    storage.add_stack(session, dest, quantity=quantity, item=item)
    touch(char)
    session.flush()
    return BuyResult(
        display_name=item.name,
        quantity=quantity,
        total_cp=total,
        pay_from=pay_from,
        destination_name=where,
        balance_cp=left,
    )


def _fmt_cp(cp: int) -> str:
    return format_cp(cp) or "0cp"


# ---------- Spells ----------


# The spellcasting stat picks the spell list a character draws from:
# Wizards cast with INT, Priests with WIS. (No alignment gating.)
_SPELL_CLASS_BY_ABILITY = {"int": "wizard", "wis": "priest"}


def spell_class_for(char: PlayerCharacter) -> str | None:
    """The spell class a character can learn from, or None if they aren't a
    caster (no spellcasting stat set)."""
    return _SPELL_CLASS_BY_ABILITY.get(char.spell_ability or "")


def addable_spells(
    session: Session, char: PlayerCharacter, cls: str, tier: int | None = None
) -> list[Spell]:
    """Reference spells of the character's class (and tier, if given) not already
    known. Any tier is learnable — scrolls and the like can grant higher-tier
    spells — so tiers are never gated by character level."""
    known_ids = {s.spell_id for s in char.spells if s.spell_id is not None}
    stmt = select(Spell).where(Spell.classes.contains(cls))
    if tier is not None:
        stmt = stmt.where(Spell.tier == tier)
    stmt = stmt.order_by(Spell.tier, Spell.name)
    return [sp for sp in session.scalars(stmt).all() if sp.id not in known_ids]


def learn_spell(session: Session, user_id: str, spell_name: str) -> Spell:
    """Learn a reference spell of the character's class. Any tier is allowed;
    only the class is checked (no alignment gating)."""
    char = require_character(session, user_id)
    cls = spell_class_for(char)
    if cls is None:
        raise CharacterError(
            "Your character isn't a spellcaster — set a "
            "spellcasting stat (INT or WIS) via **Edit Stats**."
        )
    sp = session.scalar(select(Spell).where(Spell.name == spell_name))
    if sp is None:
        raise CharacterError("That spell no longer exists.")
    if cls not in sp.class_list:
        allowed = " / ".join(c.capitalize() for c in sp.class_list)
        raise CharacterError(
            f"**{sp.name}** is a {allowed} spell; your character casts {cls} spells."
        )
    if any(s.spell_id == sp.id for s in char.spells):
        raise CharacterError(f"You already know **{sp.name}**.")
    # Append through the relationship so `char.spells` stays current within
    # this transaction (a second learn in the same session sees it).
    char.spells.append(CharacterSpell(spell_id=sp.id))
    touch(char)
    session.flush()
    return sp


def forget_spell(session: Session, user_id: str, cs_id: int) -> str:
    """Forget a known spell. Returns its display name."""
    char = require_character(session, user_id)
    cs = find_known_spell(char, cs_id)
    if cs is None:
        raise CharacterError("You don't know that spell.")
    display = cs.display_name
    char.spells.remove(cs)  # delete-orphan cascade deletes the row
    touch(char)
    session.flush()
    return display


def set_spell_lost(
    session: Session, user_id: str, cs_id: int, lost: bool
) -> CharacterSpell:
    """Mark a known spell lost (failed casting check) or available again."""
    char = require_character(session, user_id)
    cs = find_known_spell(char, cs_id)
    if cs is None:
        raise CharacterNotFound("You don't know that spell.")
    cs.lost = lost
    touch(char)
    session.flush()
    return cs


def rest(session: Session, user_id: str) -> int:
    """A rest restores every lost spell. Returns how many came back."""
    char = require_character(session, user_id)
    lost = [s for s in char.spells if s.lost]
    for s in lost:
        s.lost = False
    if lost:
        touch(char)
        session.flush()
    return len(lost)


# ---------- Effects (durations and temporary stat changes) ----------

MAX_EFFECT_ROUNDS = 100
EFFECT_NAME_MAX = 60
_BONUS_RE = re.compile(r"^([+\-=])\s*(\d{1,3})$")
# Largest |N| for "+N"/"-N" per stat (default 10), and the range for "=N".
_ADD_LIMIT = {"hp": 99}
_SET_RANGE = {"ac": (0, 40), "hp": (0, 999)}


def parse_bonus(key: str, raw: object) -> str | None:
    """Normalise one bonus entry: "+3", "-1" or "=14" (a bare number adds).
    Blank means no change. Raises CharacterError when invalid."""
    text = str(raw if raw is not None else "").strip().replace("−", "-")
    if not text:
        return None
    if text.isdigit():
        text = "+" + text
    m = _BONUS_RE.match(text)
    if m is None:
        raise CharacterError(
            f"{key.upper()}: use +2 to add, -1 to subtract or =14 to set."
        )
    op, n = m.group(1), int(m.group(2))
    if op == "=":
        lo, hi = _SET_RANGE.get(key, (-10, 10))
        if not lo <= n <= hi:
            raise CharacterError(f"{key.upper()}: =N must be {lo} to {hi}.")
        return f"={n}"
    limit = _ADD_LIMIT.get(key, 10)
    if n > limit:
        raise CharacterError(f"{key.upper()}: adjust by at most {limit}.")
    return f"{op}{n}"


def clean_bonuses(raw: dict | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in (raw or {}).items():
        if key not in EFFECT_BONUS_KEYS:
            raise CharacterError(f"Unknown stat {key!r}.")
        parsed = parse_bonus(key, value)
        if parsed is not None:
            out[key] = parsed
    return out


def _clean_name(name: str | None) -> str:
    name = (name or "").strip()
    if not name:
        raise CharacterError("Give the effect a name.")
    if len(name) > EFFECT_NAME_MAX:
        raise CharacterError(f"Effect names are at most {EFFECT_NAME_MAX} characters.")
    return name


def _clean_rounds(n: int | None) -> int | None:
    if n is None:
        return None
    if not 1 <= n <= MAX_EFFECT_ROUNDS:
        raise CharacterError(f"Rounds must be 1 to {MAX_EFFECT_ROUNDS}.")
    return n


def find_effect(char: PlayerCharacter, effect_id: int) -> CharacterEffect | None:
    return next((e for e in char.effects if e.id == effect_id), None)


def _own_effect(char: PlayerCharacter, effect_id: int) -> CharacterEffect:
    eff = find_effect(char, effect_id)
    if eff is None:
        raise CharacterNotFound("That effect is no longer there.")
    return eff


def add_effect(
    session: Session,
    user_id: str,
    name: str,
    max_rounds: int | None = None,
    bonuses: dict | None = None,
) -> CharacterEffect:
    char = require_character(session, user_id)
    cleaned = clean_bonuses(bonuses)
    eff = CharacterEffect(
        name=_clean_name(name),
        max_rounds=_clean_rounds(max_rounds),
        bonuses=json.dumps(cleaned) if cleaned else None,
    )
    char.effects.append(eff)
    touch(char)
    session.flush()
    return eff


def update_effect(
    session: Session, user_id: str, effect_id: int, **fields
) -> CharacterEffect:
    """Change only the fields passed: name, max_rounds (None = no duration),
    rounds_used, bonuses (replaces the whole set)."""
    char = require_character(session, user_id)
    eff = _own_effect(char, effect_id)
    if "name" in fields:
        eff.name = _clean_name(fields["name"])
    if "max_rounds" in fields:
        eff.max_rounds = _clean_rounds(fields["max_rounds"])
        eff.rounds_used = (
            0 if eff.max_rounds is None else min(eff.rounds_used, eff.max_rounds)
        )
    if "bonuses" in fields:
        cleaned = clean_bonuses(fields["bonuses"])
        eff.bonuses = json.dumps(cleaned) if cleaned else None
    if "rounds_used" in fields:
        used = fields["rounds_used"]
        if eff.max_rounds is None:
            raise CharacterError("This effect has no duration to tick.")
        if used is None or not 0 <= used <= eff.max_rounds:
            raise CharacterError(f"Rounds used must be 0 to {eff.max_rounds}.")
        eff.rounds_used = used
    touch(char)
    session.flush()
    return eff


def remove_effect(session: Session, user_id: str, effect_id: int) -> str:
    char = require_character(session, user_id)
    eff = _own_effect(char, effect_id)
    name = eff.name
    char.effects.remove(eff)
    touch(char)
    session.flush()
    return name


@dataclass
class StatResult:
    base: int
    total: int
    # (effect name, "+3" / "=14") for each active effect that changes this stat
    sources: list[tuple[str, str]] = field(default_factory=list)


def resolve_stat(effects: Sequence[CharacterEffect], key: str, base: int) -> StatResult:
    """Apply active effects to one stat: the highest "=N" replaces the base,
    then every "+N"/"-N" is added. Ended effects are ignored."""
    sets: list[int] = []
    adds = 0
    sources: list[tuple[str, str]] = []
    for e in effects:
        if e.ended:
            continue
        text = e.bonus_map().get(key)
        if not text:
            continue
        sources.append((e.name, text))
        if text.startswith("="):
            sets.append(int(text[1:]))
        else:
            adds += int(text)
    start = max(sets) if sets else base
    return StatResult(base=base, total=start + adds, sources=sources)
