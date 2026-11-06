"""The item catalog: adding and editing entries (shared by the web API and the
`/items` commands where they share a rule).

Same conventions as `services.characters`: the caller owns the transaction, and
rule failures raise `CharacterError` with a user-facing reason.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shadowdark_bot.currency import parse_value_string
from shadowdark_bot.models import (
    ITEM_TYPE_MAGICAL,
    ITEM_TYPES,
    InventoryEntry,
    Item,
    TreasuryEntry,
)
from shadowdark_bot.services.characters import CharacterError

# Lines the Discord item embed renders on its own; some saved descriptions
# have them pasted in.
_RENDERED_FIELD_PREFIXES = (
    "**Gear slots:**",
    "**Type:**",
    "**Value:**",
    "**Status:**",
)


def clean_description(text: str | None) -> str | None:
    """Drop the lines the item embed renders itself. None when nothing is left."""
    if not text:
        return None
    kept = [
        line
        for line in text.split("\n")
        if not line.strip().startswith(_RENDERED_FIELD_PREFIXES)
    ]
    return "\n".join(kept).strip() or None


def type_change_error(session: Session, item: Item, new_type: str | None) -> str | None:
    """Return an error string if changing item.item_type to new_type is blocked
    by existing inventory/treasury references, else None."""
    if new_type is None or new_type == item.item_type:
        return None
    going_magical = new_type == ITEM_TYPE_MAGICAL
    leaving_magical = item.item_type == ITEM_TYPE_MAGICAL
    if going_magical:
        blocking = session.scalar(
            select(func.count())
            .select_from(InventoryEntry)
            .where(InventoryEntry.item_id == item.id)
        ) or 0
        if blocking:
            return (
                f"Cannot mark **{item.name}** as magical — it still has "
                f"{blocking} inventory stack(s). Remove them first."
            )
    elif leaving_magical:
        blocking = session.scalar(
            select(func.count())
            .select_from(TreasuryEntry)
            .where(TreasuryEntry.item_id == item.id)
        ) or 0
        if blocking:
            return (
                f"Cannot change **{item.name}** from magical — it still "
                f"has {blocking} treasury instance(s). Remove them first."
            )
    return None


def _validate(
    session: Session,
    *,
    name: str,
    item_type: str,
    gear_slots: float,
    bundle_size: int,
    value: str | None,
    exclude_id: int | None = None,
) -> tuple[str, int | None]:
    """Shared create/update rules. Returns (clean name, value in copper);
    `value=None` skips the value check and gives None."""
    clean = (name or "").strip()
    if not clean:
        raise CharacterError("Name cannot be empty.")
    if item_type not in ITEM_TYPES:
        raise CharacterError("Choose an item type.")
    if gear_slots < 0:
        raise CharacterError("Gear slots must be ≥ 0.")
    if bundle_size < 1:
        raise CharacterError("Bundle size must be ≥ 1.")
    value_cp = None
    if value is not None:
        try:
            value_cp = parse_value_string(value)
        except ValueError as err:
            raise CharacterError(str(err)) from err
    stmt = select(Item.id).where(func.lower(Item.name) == clean.lower())
    if exclude_id is not None:
        stmt = stmt.where(Item.id != exclude_id)
    if session.scalar(stmt) is not None:
        raise CharacterError(f"An item named **{clean}** already exists.")
    return clean, value_cp


def create_item(
    session: Session,
    user_id: str,
    *,
    name: str,
    item_type: str,
    description: str | None = None,
    gear_slots: float | None = None,
    bundle_size: int | None = None,
    value: str | None = None,
) -> Item:
    """Add a catalog item. Gear slots default to 1 and the bundle size to 1;
    `value` is a string like "5gp 2sp 3cp" (blank = no value tracked)."""
    slots = 1.0 if gear_slots is None else float(gear_slots)
    bundle = 1 if bundle_size is None else int(bundle_size)
    clean, value_cp = _validate(
        session,
        name=name,
        item_type=item_type,
        gear_slots=slots,
        bundle_size=bundle,
        value=value or "",
    )
    item = Item(
        name=clean,
        description=(description or "").strip() or None,
        gear_slots=slots,
        bundle_size=bundle,
        value_cp=value_cp,
        item_type=item_type,
        created_by=user_id,
    )
    session.add(item)
    session.flush()
    return item


def update_item(
    session: Session,
    item_id: int,
    *,
    name: str | None = None,
    item_type: str | None = None,
    description: str | None = None,
    gear_slots: float | None = None,
    bundle_size: int | None = None,
    value: str | None = None,
) -> Item:
    """Edit a catalog item. None leaves a field alone; `description=""` and
    `value=""` clear it."""
    item = session.get(Item, item_id)
    if item is None:
        raise CharacterError("That item no longer exists.")
    new_type = item_type if item_type is not None else item.item_type
    slots = item.gear_slots if gear_slots is None else float(gear_slots)
    bundle = item.bundle_size if bundle_size is None else int(bundle_size)
    clean, value_cp = _validate(
        session,
        name=item.name if name is None else name,
        item_type=new_type,
        gear_slots=slots,
        bundle_size=bundle,
        value=value,
        exclude_id=item.id,
    )
    err = type_change_error(session, item, new_type)
    if err:
        raise CharacterError(err)
    item.name = clean
    item.item_type = new_type
    item.gear_slots = slots
    item.bundle_size = bundle
    if value is not None:
        item.value_cp = value_cp
    if description is not None:
        item.description = description.strip() or None
    session.flush()
    return item
