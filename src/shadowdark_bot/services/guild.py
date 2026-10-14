"""Guild-side operations: taking from shared storage and the magic item treasury.

Same conventions as `services.characters`: the caller owns the transaction, and
rule failures raise `CharacterError` with a user-facing reason.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from shadowdark_bot import storage
from shadowdark_bot.models import (
    LOCATION_ROLE_HELD,
    LOCATION_ROLE_STASH,
    Borrow,
    InventoryEntry,
    Item,
    Location,
    PlayerCharacter,
    TreasuryEntry,
)
from shadowdark_bot.services import characters
from shadowdark_bot.services.characters import CharacterError

STATUS_AVAILABLE = "available"
STATUS_BORROWED = "borrowed"


def _naive_utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


# ---------- Guild storage ----------


@dataclass
class TakeResult:
    display_name: str
    quantity: int
    target_name: str


def take(
    session: Session,
    user_id: str,
    *,
    location_id: int,
    entry_id: int,
    quantity: int | None = None,
    role: str = LOCATION_ROLE_HELD,
) -> TakeResult:
    """Move a stack (or `quantity` copies) out of a shared guild storage location
    into the character's held items or stash, capacity-checked like `give`."""
    if quantity is not None and quantity < 1:
        raise CharacterError("Quantity must be ≥ 1.")
    if role not in (LOCATION_ROLE_HELD, LOCATION_ROLE_STASH):
        raise CharacterError("Choose Carrying or Stash.")
    char = characters.require_character(session, user_id)
    source = session.get(Location, location_id)
    if source is None or source.kind != "inventory" or source.owner_user_id is not None:
        raise CharacterError("That isn't a guild storage location.")
    entry = session.get(InventoryEntry, entry_id)
    if entry is None or entry.location_id != source.id:
        raise CharacterError("That item is no longer there.")
    take_qty = entry.quantity if quantity is None else quantity
    if take_qty > entry.quantity:
        raise CharacterError(
            f"**{source.name}** only has {entry.quantity}× **{entry.display_name}**."
        )
    held, stash = storage.ensure_character_locations(session, char)
    target = held if role == LOCATION_ROLE_HELD else stash
    display = entry.display_name
    characters.move_stack(session, entry, target, take_qty)
    characters.touch(char)
    session.flush()
    return TakeResult(display_name=display, quantity=take_qty, target_name=target.name)


# ---------- Magic item treasury ----------


def open_borrow(session: Session, treasury_entry_id: int) -> Borrow | None:
    return session.scalar(
        select(Borrow).where(
            Borrow.treasury_entry_id == treasury_entry_id,
            Borrow.returned_at.is_(None),
        )
    )


class AlreadyBorrowed(CharacterError):
    """The treasury entry is out; `borrower_id` is who has it (None if unrecorded)."""

    def __init__(self, entry_id: int, borrower_id: str | None) -> None:
        super().__init__(f"#{entry_id} is already borrowed.")
        self.borrower_id = borrower_id


def _add_borrowed_to_inventory(session: Session, borrower_id: str, item_id: int) -> None:
    """If the borrower has a character, add one of the borrowed catalog item to
    their held location (merging into an existing stack; no capacity check — a
    loan is never refused for space). No-op otherwise — the borrow is still
    recorded in the ledger either way."""
    char = session.scalar(
        select(PlayerCharacter).where(PlayerCharacter.user_id == borrower_id)
    )
    if char is None:
        return
    held, _stash = storage.ensure_character_locations(session, char)
    item = session.get(Item, item_id)
    if item is None:
        return
    storage.add_stack(session, held, quantity=1, item=item)
    char.updated_at = _naive_utcnow()


def _remove_borrowed_from_inventory(
    session: Session, borrower_id: str, item_id: int
) -> None:
    """If the borrower's held location carries the returned catalog item, drop
    one (removing the stack when it empties)."""
    char = session.scalar(
        select(PlayerCharacter).where(PlayerCharacter.user_id == borrower_id)
    )
    if char is None:
        return
    held = storage.held_location(session, borrower_id)
    if held is None:
        return
    stack = storage.find_catalog_stack(session, held.id, item_id)
    if stack is None:
        return
    if stack.quantity <= 1:
        session.delete(stack)
    else:
        stack.quantity -= 1
    char.updated_at = _naive_utcnow()


def borrow(session: Session, entry_id: int, borrower_id: str) -> TreasuryEntry:
    """Lend a treasury entry to `borrower_id`. If they have a character the item
    appears in their held items too. Raises `AlreadyBorrowed` if it's out."""
    entry = session.get(TreasuryEntry, entry_id)
    if entry is None:
        raise CharacterError(f"No treasury entry with id {entry_id}.")
    if entry.status == STATUS_BORROWED:
        current = open_borrow(session, entry.id)
        raise AlreadyBorrowed(entry_id, current.borrower_id if current else None)
    entry.status = STATUS_BORROWED
    session.add(Borrow(treasury_entry_id=entry.id, borrower_id=borrower_id))
    _add_borrowed_to_inventory(session, borrower_id, entry.item_id)
    session.flush()
    return entry


def return_borrow(
    session: Session, entry_id: int, *, only_borrower: str | None = None
) -> TreasuryEntry:
    """Close the open borrow on a treasury entry and take one copy back out of
    the borrower's held items. `only_borrower` restricts who may return it (the
    web app passes the signed-in user; the bot lets anyone return)."""
    entry = session.get(TreasuryEntry, entry_id)
    if entry is None:
        raise CharacterError(f"No treasury entry with id {entry_id}.")
    if entry.status != STATUS_BORROWED:
        raise CharacterError(f"#{entry_id} isn't currently borrowed.")
    current = open_borrow(session, entry.id)
    if current is None:
        raise CharacterError(f"#{entry_id} has no open borrow record.")
    if only_borrower is not None and current.borrower_id != only_borrower:
        raise CharacterError("Only the person who borrowed it can return it.")
    current.returned_at = _naive_utcnow()
    entry.status = STATUS_AVAILABLE
    _remove_borrowed_from_inventory(session, current.borrower_id, entry.item_id)
    session.flush()
    return entry
