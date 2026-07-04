"""Shared helpers for location-backed storage.

A character owns two `Location` rows: a *held* location (its carried inventory,
capacity = max(10, STR)) and a *stash* (10 slots). Both hold `InventoryEntry`
stacks — the same unified catalog/freeform model the guild `/inventory` uses.
These helpers are shared by the character and treasury cogs so the two systems
stay consistent.
"""

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, joinedload

from shadowdark_bot.models import (
    LOCATION_ROLE_HELD,
    LOCATION_ROLE_STASH,
    InventoryEntry,
    Location,
    PlayerCharacter,
)
from shadowdark_bot.rules import carry_capacity


def _held_name(char: PlayerCharacter) -> str:
    return char.name


def _stash_name(char: PlayerCharacter) -> str:
    return f"{char.name} Guild Stash"


def character_location(
    session: Session, user_id: str, role: str
) -> Location | None:
    return session.scalar(
        select(Location).where(
            Location.owner_user_id == user_id, Location.role == role
        )
    )


def held_location(session: Session, user_id: str) -> Location | None:
    return character_location(session, user_id, LOCATION_ROLE_HELD)


def stash_location(session: Session, user_id: str) -> Location | None:
    return character_location(session, user_id, LOCATION_ROLE_STASH)


def ensure_character_locations(
    session: Session, char: PlayerCharacter
) -> tuple[Location, Location]:
    """Return (held, stash), creating either that's missing. Also keeps their
    names/descriptions and the held capacity in step with the character."""
    held = held_location(session, char.user_id)
    stash = stash_location(session, char.user_id)
    held_cap = float(carry_capacity(char.str_score))
    if held is None:
        held = Location(
            name=_held_name(char),
            kind="inventory",
            description=f"Currently held items for {char.name}",
            max_gear_slots=held_cap,
            owner_user_id=char.user_id,
            role=LOCATION_ROLE_HELD,
        )
        session.add(held)
    else:
        held.name = _held_name(char)
        held.description = f"Currently held items for {char.name}"
        held.max_gear_slots = held_cap
    if stash is None:
        stash = Location(
            name=_stash_name(char),
            kind="inventory",
            description=f"Stored items for {char.name}",
            max_gear_slots=10.0,
            owner_user_id=char.user_id,
            role=LOCATION_ROLE_STASH,
        )
        session.add(stash)
    else:
        stash.name = _stash_name(char)
        stash.description = f"Stored items for {char.name}"
    session.flush()
    return held, stash


def delete_character_locations(session: Session, user_id: str) -> None:
    """Remove a character's owned locations and everything in them."""
    locs = session.scalars(
        select(Location).where(Location.owner_user_id == user_id)
    ).all()
    for loc in locs:
        session.execute(
            delete(InventoryEntry).where(InventoryEntry.location_id == loc.id)
        )
        session.delete(loc)


def location_entries(session: Session, location_id: int) -> list[InventoryEntry]:
    """All stacks at a location (catalog item eager-loaded), name-sorted."""
    entries = list(
        session.scalars(
            select(InventoryEntry)
            .options(joinedload(InventoryEntry.item))
            .where(InventoryEntry.location_id == location_id)
        ).all()
    )
    return sorted(entries, key=lambda e: e.display_name.lower())


def used_slots(entries: list[InventoryEntry]) -> float:
    return float(sum(e.slot_cost for e in entries))


def find_catalog_stack(
    session: Session, location_id: int, item_id: int
) -> InventoryEntry | None:
    return session.scalar(
        select(InventoryEntry).where(
            InventoryEntry.location_id == location_id,
            InventoryEntry.item_id == item_id,
        )
    )


def find_freeform_stack(
    session: Session, location_id: int, name: str
) -> InventoryEntry | None:
    for entry in location_entries(session, location_id):
        if entry.item_id is None and (entry.name or "").lower() == name.lower():
            return entry
    return None


def add_stack(
    session: Session,
    location: Location,
    *,
    quantity: int,
    item: object | None = None,
    freeform_name: str | None = None,
    slots_each: float = 1.0,
    bundle_size: int = 1,
    notes: str | None = None,
) -> InventoryEntry:
    """Add `quantity` of a catalog `item` or a freeform stack to `location`,
    merging into an existing stack when present. Capacity is the caller's job."""
    if item is not None:
        stack = find_catalog_stack(session, location.id, item.id)
        if stack is None:
            stack = InventoryEntry(
                location_id=location.id, item_id=item.id, quantity=quantity
            )
            session.add(stack)
        else:
            stack.quantity += quantity
    else:
        assert freeform_name is not None
        stack = find_freeform_stack(session, location.id, freeform_name)
        if stack is None:
            stack = InventoryEntry(
                location_id=location.id,
                name=freeform_name,
                quantity=quantity,
                slots_each=slots_each,
                bundle_size=bundle_size,
                notes=(notes or None),
            )
            session.add(stack)
        else:
            stack.quantity += quantity
    session.flush()
    return stack
