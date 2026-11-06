"""The creature bestiary: monsters the party has met, and each encounter.

Every stat is optional — `None` means not known, and is not shown. Callers
pass only the fields they want to change; a blank or `None` value sets that
field back to unset. A creature is Named or a Type; a Named creature may
point at its Type and then shows the Type's stats where its own are blank
(see `inherited`). Tags are lowercase words. An encounter is one free-text
note. Failures raise CreatureError (a CharacterError, so the web layer
answers 400) or CreatureNotFound (404).
"""

import json
import re
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from shadowdark_bot.models import Creature, CreatureEncounter
from shadowdark_bot.services import effects
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
    "title": ("Class / title", 100),
    "attacks": ("Attacks", 500),
    "movement": ("Movement", 100),
    "special_abilities": ("Special abilities", 4000),
    "description": ("Description", 4000),
    "notes": ("Notes", 4000),
}
NAME_MAX = 200
ENCOUNTER_TEXT: dict[str, tuple[str, int]] = {"details": ("Encounter", 2000)}
KINDS = ("named", "type")
TAG_MAX = 30
TAGS_MAX = 12
DEFAULT_TAGS = ("ally", "enemy", "neutral", "creature", "townfolk")
# What a Named creature shows from its Type when its own value is blank.
INHERITED_FIELDS = (*INT_FIELDS, "attacks", "movement", "alignment", "special_abilities")


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


def _clean_tags(value) -> str | None:
    """A list (or comma-separated string) of tags as JSON text, or None."""
    if _blank(value):
        return None
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)):
        raise CreatureError("Tags must be a list of words.")
    tags: list[str] = []
    for raw in value:
        tag = re.sub(r"\s+", " ", str(raw).strip().lower())
        if not tag:
            continue
        if "," in tag:
            raise CreatureError("A tag can't contain a comma.")
        if len(tag) > TAG_MAX:
            raise CreatureError(f"A tag is too long (max {TAG_MAX} characters).")
        if tag not in tags:
            tags.append(tag)
    if len(tags) > TAGS_MAX:
        raise CreatureError(f"Too many tags (max {TAGS_MAX}).")
    return json.dumps(tags) if tags else None


def _clean_kind(value) -> str:
    kind = "" if _blank(value) else str(value).strip().lower()
    if kind not in KINDS:
        raise CreatureError("Kind must be Named or Type.")
    return kind


def _clean_fields(data: dict) -> dict:
    """Validate the given fields only, returning column values. `type_id` is
    passed through for `_apply` to check against the final kind."""
    out: dict = {}
    for key, value in data.items():
        if key == "name":
            out[key] = _clean_name(value)
        elif key == "tags":
            out[key] = _clean_tags(value)
        elif key == "kind":
            out[key] = _clean_kind(value)
        elif key == "type_id":
            out[key] = None if _blank(value) else value
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


def _check_type_link(session: Session, creature: Creature) -> None:
    """The creature's final kind and type_id must agree."""
    if creature.kind == "type":
        creature.type_id = None
        return
    if creature.type_id is None:
        return
    try:
        type_id = int(creature.type_id)
    except (TypeError, ValueError):
        raise CreatureError("That type isn't in the bestiary.") from None
    target = session.get(Creature, type_id)
    if target is None:
        raise CreatureError("That type isn't in the bestiary.")
    if target.kind != "type" or target.id == creature.id:
        raise CreatureError("Only a Type can be linked.")
    creature.type_id = target.id


def _apply(session: Session, creature: Creature, fields: dict) -> None:
    """Set the validated fields, then check the kind and type link together
    (so a call may set `kind` and `type_id` at once)."""
    if fields.get("type_id") is not None and (fields.get("kind") or creature.kind) != "named":
        raise CreatureError("Only a Named creature can have a type.")
    if fields.get("kind") == "named" and creature.id is not None:
        used = session.scalar(
            select(func.count()).select_from(Creature).where(Creature.type_id == creature.id)
        )
        if used:
            plural = "creature uses" if used == 1 else "creatures use"
            raise CreatureError(f"{used} named {plural} {creature.name} as their type.")
    for key, value in fields.items():
        setattr(creature, key, value)
    _check_type_link(session, creature)


def create_creature(session: Session, user_id: str, **data) -> Creature:
    """Add a creature. Only the name is required; the kind defaults to Type."""
    if "name" not in data:
        raise CreatureError("A creature needs a name (a description will do).")
    if _blank(data.get("kind")):
        data = {k: v for k, v in data.items() if k != "kind"}
    fields = {"kind": "type", **_clean_fields(data)}
    creature = Creature(created_by=user_id)
    _apply(session, creature, fields)
    session.add(creature)
    session.flush()
    return creature


def update_creature(session: Session, creature_id: int, **changes) -> Creature:
    """Change only the given fields; blank/None sets a stat back to unknown
    (the name can't be blanked)."""
    creature = get_creature(session, creature_id)
    _apply(session, creature, _clean_fields(changes))
    creature.updated_at = datetime.now()
    session.flush()
    return creature


def delete_creature(session: Session, creature_id: int) -> str:
    """Delete a creature and its encounters; returns its name."""
    creature = get_creature(session, creature_id)
    name = creature.name
    effects.drop_links_for(session, "creature", creature.id)
    session.execute(
        update(Creature).where(Creature.type_id == creature.id).values(type_id=None)
    )
    session.delete(creature)
    session.flush()
    return name


def list_creatures(session: Session, q: str = "") -> list[tuple[Creature, int]]:
    """Creatures by name with their encounter count, optionally filtered by a
    name substring."""
    stats = (
        select(
            CreatureEncounter.creature_id.label("cid"),
            func.count().label("n"),
        )
        .group_by(CreatureEncounter.creature_id)
        .subquery()
    )
    stmt = (
        select(Creature, stats.c.n)
        .outerjoin(stats, stats.c.cid == Creature.id)
        .order_by(func.lower(Creature.name), Creature.id)
    )
    if q.strip():
        needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(Creature.name.ilike(f"%{needle}%", escape="\\"))
    return [(c, n or 0) for c, n in session.execute(stmt).all()]


def tag_suggestions(session: Session) -> list[str]:
    """The default tags in order, then every other tag in use, alphabetical."""
    used: set[str] = set()
    for (raw,) in session.execute(select(Creature.tags).where(Creature.tags.is_not(None))):
        used.update(json.loads(raw))
    return [*DEFAULT_TAGS, *sorted(used - set(DEFAULT_TAGS))]


def inherited(session: Session, creature: Creature) -> dict:
    """Values a Named creature shows from its Type: each inheritable field
    that is blank on the creature and set on the Type."""
    if creature.kind != "named" or creature.type_id is None:
        return {}
    parent = session.get(Creature, creature.type_id)
    if parent is None:
        return {}
    return {
        field: getattr(parent, field)
        for field in INHERITED_FIELDS
        if getattr(creature, field) is None and getattr(parent, field) is not None
    }


# ---------- encounters ----------


def _clean_encounter(data: dict) -> dict:
    out: dict = {}
    for key, value in data.items():
        if key in ENCOUNTER_TEXT:
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
    """Record a meeting; `data` holds `details`, the note (required)."""
    creature = get_creature(session, creature_id)
    fields = _clean_encounter(data or {})
    if not fields.get("details"):
        raise CreatureError("Write what happened.")
    enc = CreatureEncounter(creature_id=creature.id, created_by=user_id, **fields)
    session.add(enc)
    session.flush()
    return enc


def update_encounter(
    session: Session, creature_id: int, encounter_id: int, changes: dict
) -> CreatureEncounter:
    enc = _get_encounter(session, creature_id, encounter_id)
    fields = _clean_encounter(changes)
    if "details" in fields and not fields["details"]:
        raise CreatureError("Write what happened.")
    for key, value in fields.items():
        setattr(enc, key, value)
    session.flush()
    return enc


def delete_encounter(session: Session, creature_id: int, encounter_id: int) -> None:
    enc = _get_encounter(session, creature_id, encounter_id)
    session.delete(enc)
    session.flush()
