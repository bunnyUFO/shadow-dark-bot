"""The creature bestiary: monsters the party has met, and each encounter.

Every stat is optional — `None` means unknown. Callers pass only the fields
they want to change; a blank or `None` value sets that field back to unknown.
Failures raise CreatureError (a CharacterError, so the web layer answers 400)
or CreatureNotFound (404).
"""

from datetime import date, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from shadowdark_bot.models import Creature, CreatureEncounter
from shadowdark_bot.services.characters import CharacterError, CharacterNotFound

ALIGNMENTS = ("L", "N", "C")

# field -> (label, minimum, maximum)
INT_FIELDS: dict[str, tuple[str, int, int | None]] = {
    "level": ("Level", 0, 30),
    "ac": ("AC", 1, 30),
    "hp": ("HP", 0, None),
    "str_mod": ("STR", -5, 10),
    "dex_mod": ("DEX", -5, 10),
    "con_mod": ("CON", -5, 10),
    "int_mod": ("INT", -5, 10),
    "wis_mod": ("WIS", -5, 10),
    "cha_mod": ("CHA", -5, 10),
}
# field -> (label, max length)
TEXT_FIELDS: dict[str, tuple[str, int]] = {
    "attacks": ("Attacks", 500),
    "movement": ("Movement", 100),
    "special_abilities": ("Special abilities", 4000),
    "description": ("Description", 4000),
    "notes": ("Notes", 4000),
}
NAME_MAX = 200
ENCOUNTER_TEXT: dict[str, tuple[str, int]] = {
    "session": ("Session", 100),
    "place": ("Place", 200),
    "outcome": ("Outcome", 2000),
}


class CreatureError(CharacterError):
    """A validation failure; `str(err)` is the user-facing reason."""


class CreatureNotFound(CharacterNotFound):
    pass


# ---------- validation ----------


def _blank(value) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _clean_int(field: str, value) -> int | None:
    label, low, high = INT_FIELDS[field]
    if _blank(value):
        return None
    if isinstance(value, bool):
        raise CreatureError(f"{label} must be a number.")
    try:
        number = value if isinstance(value, int) else int(str(value).strip())
    except ValueError:
        raise CreatureError(f"{label} must be a whole number.") from None
    if number < low or (high is not None and number > high):
        if high is None:
            raise CreatureError(f"{label} must be {low} or more.")
        raise CreatureError(f"{label} must be between {low} and {high}.")
    return number


def _clean_text(label: str, limit: int, value) -> str | None:
    if _blank(value):
        return None
    text = str(value).strip()
    if len(text) > limit:
        raise CreatureError(f"{label} is too long (max {limit} characters).")
    return text


def _clean_name(value) -> str:
    name = _clean_text("Name", NAME_MAX, value)
    if name is None:
        raise CreatureError("A creature needs a name (a description will do).")
    return name


def _clean_alignment(value) -> str | None:
    if _blank(value):
        return None
    letter = str(value).strip().upper()[:1]
    if letter not in ALIGNMENTS:
        raise CreatureError("Alignment must be L, N or C.")
    return letter


def _clean_fields(data: dict) -> dict:
    """Validate the given fields only, returning column values."""
    out: dict = {}
    for key, value in data.items():
        if key == "name":
            out[key] = _clean_name(value)
        elif key == "alignment":
            out[key] = _clean_alignment(value)
        elif key in INT_FIELDS:
            out[key] = _clean_int(key, value)
        elif key in TEXT_FIELDS:
            label, limit = TEXT_FIELDS[key]
            out[key] = _clean_text(label, limit, value)
        else:
            raise CreatureError(f"Unknown field: {key}.")
    return out


# ---------- creatures ----------


def get_creature(session: Session, creature_id: int) -> Creature:
    creature = session.get(Creature, creature_id)
    if creature is None:
        raise CreatureNotFound("That creature isn't in the bestiary.")
    return creature


def create_creature(session: Session, user_id: str, **data) -> Creature:
    """Add a creature. Only the name is required."""
    if "name" not in data:
        raise CreatureError("A creature needs a name (a description will do).")
    creature = Creature(created_by=user_id, **_clean_fields(data))
    session.add(creature)
    session.flush()
    return creature


def update_creature(session: Session, creature_id: int, **changes) -> Creature:
    """Change only the given fields; blank/None sets a stat back to unknown
    (the name can't be blanked)."""
    creature = get_creature(session, creature_id)
    for key, value in _clean_fields(changes).items():
        setattr(creature, key, value)
    creature.updated_at = datetime.now()
    session.flush()
    return creature


def delete_creature(session: Session, creature_id: int) -> str:
    """Delete a creature and its encounters; returns its name."""
    creature = get_creature(session, creature_id)
    name = creature.name
    session.delete(creature)
    session.flush()
    return name


def list_creatures(
    session: Session, q: str = ""
) -> list[tuple[Creature, int, date | None]]:
    """Creatures by name with (encounter count, last seen date), optionally
    filtered by a name substring."""
    stats = (
        select(
            CreatureEncounter.creature_id.label("cid"),
            func.count().label("n"),
            func.max(CreatureEncounter.date).label("last"),
        )
        .group_by(CreatureEncounter.creature_id)
        .subquery()
    )
    stmt = (
        select(Creature, stats.c.n, stats.c.last)
        .outerjoin(stats, stats.c.cid == Creature.id)
        .order_by(func.lower(Creature.name), Creature.id)
    )
    if q.strip():
        needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Creature.name.ilike(f"%{needle}%", escape="\\"))
    return [(c, n or 0, last) for c, n, last in session.execute(stmt).all()]


# ---------- encounters ----------


def _clean_date(value) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip())
        except ValueError:
            pass
    raise CreatureError("Date must look like 2026-07-04.")


def _clean_encounter(data: dict) -> dict:
    out: dict = {}
    for key, value in data.items():
        if key == "date":
            out[key] = date.today() if _blank(value) else _clean_date(value)
        elif key in ENCOUNTER_TEXT:
            label, limit = ENCOUNTER_TEXT[key]
            out[key] = _clean_text(label, limit, value)
        else:
            raise CreatureError(f"Unknown field: {key}.")
    return out


def _get_encounter(session: Session, creature_id: int, encounter_id: int):
    enc = session.get(CreatureEncounter, encounter_id)
    if enc is None or enc.creature_id != creature_id:
        raise CreatureNotFound("That encounter isn't recorded.")
    return enc


def add_encounter(
    session: Session, user_id: str, creature_id: int, data: dict | None = None
) -> CreatureEncounter:
    """Record a meeting; `data` holds date/session/place/outcome (the date
    defaults to today). A dict because "session" is also the DB session."""
    creature = get_creature(session, creature_id)
    fields = _clean_encounter(data or {})
    fields.setdefault("date", date.today())
    enc = CreatureEncounter(creature_id=creature.id, created_by=user_id, **fields)
    session.add(enc)
    session.flush()
    return enc


def update_encounter(
    session: Session, creature_id: int, encounter_id: int, changes: dict
) -> CreatureEncounter:
    enc = _get_encounter(session, creature_id, encounter_id)
    for key, value in _clean_encounter(changes).items():
        setattr(enc, key, value)
    session.flush()
    return enc


def delete_encounter(session: Session, creature_id: int, encounter_id: int) -> None:
    enc = _get_encounter(session, creature_id, encounter_id)
    session.delete(enc)
    session.flush()
