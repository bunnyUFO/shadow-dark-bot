"""The item catalog: adding entries (shared by the web API; `/items add` in
Discord has its own copy of these rules).

Same conventions as `services.characters`: the caller owns the transaction, and
rule failures raise `CharacterError` with a user-facing reason.
"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shadowdark_bot.currency import parse_value_string
from shadowdark_bot.models import ITEM_TYPES, Item
from shadowdark_bot.services.characters import CharacterError


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
    clean = (name or "").strip()
    if not clean:
        raise CharacterError("Name cannot be empty.")
    if item_type not in ITEM_TYPES:
        raise CharacterError("Choose an item type.")
    slots = 1.0 if gear_slots is None else float(gear_slots)
    if slots < 0:
        raise CharacterError("Gear slots must be ≥ 0.")
    bundle = 1 if bundle_size is None else int(bundle_size)
    if bundle < 1:
        raise CharacterError("Bundle size must be ≥ 1.")
    try:
        value_cp = parse_value_string(value or "")
    except ValueError as err:
        raise CharacterError(str(err)) from err
    if session.scalar(select(Item.id).where(func.lower(Item.name) == clean.lower())) is not None:
        raise CharacterError(f"An item named **{clean}** already exists.")
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
