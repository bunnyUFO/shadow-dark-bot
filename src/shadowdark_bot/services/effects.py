"""The effects library: reusable effect templates and their links to the items,
spells and creatures they come from.

Bonuses are parsed and validated by the same code as character effects
(`characters.clean_bonuses`). Failures raise EffectError (a CharacterError, so
the web layer answers 400) or EffectNotFound (404).
"""

import json
from datetime import datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from shadowdark_bot.models import (
    EFFECT_LINK_KINDS,
    Creature,
    EffectTemplate,
    EffectTemplateLink,
    Item,
    Spell,
)
from shadowdark_bot.services.characters import (
    EFFECT_NAME_MAX,
    MAX_EFFECT_ROUNDS,
    CharacterError,
    CharacterNotFound,
    clean_bonuses,
)

DESCRIPTION_MAX = 2000

_TARGET_MODELS = {"item": Item, "spell": Spell, "creature": Creature}


class EffectError(CharacterError):
    """A validation failure; `str(err)` is the user-facing reason."""


class EffectNotFound(CharacterNotFound):
    pass


# ---------- validation ----------


def _clean_name(value) -> str:
    name = (value or "").strip() if isinstance(value, str) or value is None else ""
    if not name:
        raise EffectError("Give the effect a name.")
    if len(name) > EFFECT_NAME_MAX:
        raise EffectError(f"Effect names are at most {EFFECT_NAME_MAX} characters.")
    return name


def _clean_description(value) -> str | None:
    text = (value or "").strip() if isinstance(value, str) or value is None else ""
    if not text:
        return None
    if len(text) > DESCRIPTION_MAX:
        raise EffectError(f"Descriptions are at most {DESCRIPTION_MAX} characters.")
    return text


def _clean_rounds(value) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise EffectError("Rounds must be a whole number.")
    if not 1 <= value <= MAX_EFFECT_ROUNDS:
        raise EffectError(f"Rounds must be 1 to {MAX_EFFECT_ROUNDS}.")
    return value


def _check_name_free(session: Session, name: str, ignore_id: int | None = None) -> None:
    stmt = select(EffectTemplate.id).where(
        func.lower(EffectTemplate.name) == name.lower()
    )
    if ignore_id is not None:
        stmt = stmt.where(EffectTemplate.id != ignore_id)
    if session.scalar(stmt) is not None:
        raise EffectError(f"There is already an effect called {name}.")


# ---------- templates ----------


def get_template(session: Session, template_id: int) -> EffectTemplate:
    template = session.get(EffectTemplate, template_id)
    if template is None:
        raise EffectNotFound("That effect isn't in the library.")
    return template


def create_template(
    session: Session,
    user_id: str | None,
    *,
    name,
    description=None,
    max_rounds=None,
    bonuses: dict | None = None,
) -> EffectTemplate:
    clean_name = _clean_name(name)
    _check_name_free(session, clean_name)
    cleaned = clean_bonuses(bonuses)
    template = EffectTemplate(
        name=clean_name,
        description=_clean_description(description),
        max_rounds=_clean_rounds(max_rounds),
        bonuses=json.dumps(cleaned) if cleaned else None,
        created_by=user_id,
    )
    session.add(template)
    session.flush()
    return template


def update_template(session: Session, template_id: int, **fields) -> EffectTemplate:
    """Change only the fields passed (name, description, max_rounds, bonuses;
    bonuses replaces the whole set)."""
    template = get_template(session, template_id)
    if "name" in fields:
        name = _clean_name(fields["name"])
        _check_name_free(session, name, ignore_id=template.id)
        template.name = name
    if "description" in fields:
        template.description = _clean_description(fields["description"])
    if "max_rounds" in fields:
        template.max_rounds = _clean_rounds(fields["max_rounds"])
    if "bonuses" in fields:
        cleaned = clean_bonuses(fields["bonuses"])
        template.bonuses = json.dumps(cleaned) if cleaned else None
    template.updated_at = datetime.now()
    session.flush()
    return template


def delete_template(session: Session, template_id: int) -> str:
    template = get_template(session, template_id)
    name = template.name
    session.delete(template)  # links go with it
    session.flush()
    return name


def list_templates(
    session: Session,
    q: str = "",
    kind: str | None = None,
    target_id: int | None = None,
) -> list[EffectTemplate]:
    """Templates by name. `q` filters by name substring; `kind` (and, with it,
    `target_id`) keeps only templates linked to that kind of source."""
    stmt = select(EffectTemplate).order_by(
        func.lower(EffectTemplate.name), EffectTemplate.id
    )
    if q.strip():
        needle = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        stmt = stmt.where(EffectTemplate.name.ilike(f"%{needle}%", escape="\\"))
    if kind is not None:
        _check_kind(kind)
        linked = select(EffectTemplateLink.template_id).where(
            EffectTemplateLink.kind == kind
        )
        if target_id is not None:
            linked = linked.where(EffectTemplateLink.target_id == target_id)
        stmt = stmt.where(EffectTemplate.id.in_(linked))
    elif target_id is not None:
        raise EffectError("Say which kind of target (item, spell or creature).")
    return list(session.scalars(stmt).all())


# ---------- links ----------


def _check_kind(kind: str) -> None:
    if kind not in EFFECT_LINK_KINDS:
        raise EffectError("Link to an item, a spell or a creature.")


def target_names(
    session: Session, links: list[EffectTemplateLink]
) -> dict[tuple[str, int], str]:
    """(kind, target id) -> name for the given links. Missing targets are
    left out (there should be none: deletes clear their links)."""
    out: dict[tuple[str, int], str] = {}
    for kind, model in _TARGET_MODELS.items():
        ids = {link.target_id for link in links if link.kind == kind}
        if ids:
            rows = session.execute(
                select(model.id, model.name).where(model.id.in_(ids))
            ).all()
            out.update({(kind, row.id): row.name for row in rows})
    return out


def add_link(
    session: Session, template_id: int, kind: str, target_id: int
) -> EffectTemplateLink:
    """Link a template to an item, spell or creature. Idempotent."""
    template = get_template(session, template_id)
    _check_kind(kind)
    if session.get(_TARGET_MODELS[kind], target_id) is None:
        raise EffectNotFound(f"That {kind} doesn't exist.")
    existing = next(
        (l for l in template.links if (l.kind, l.target_id) == (kind, target_id)),
        None,
    )
    if existing is not None:
        return existing
    link = EffectTemplateLink(kind=kind, target_id=target_id)
    template.links.append(link)
    session.flush()
    return link


def remove_link(session: Session, template_id: int, kind: str, target_id: int) -> None:
    template = get_template(session, template_id)
    _check_kind(kind)
    link = next(
        (l for l in template.links if (l.kind, l.target_id) == (kind, target_id)),
        None,
    )
    if link is None:
        raise EffectNotFound("That link isn't there.")
    template.links.remove(link)
    session.flush()


def drop_links_for(session: Session, kind: str, target_id: int) -> int:
    """Clear every link to a deleted item, spell or creature."""
    _check_kind(kind)
    result = session.execute(
        delete(EffectTemplateLink).where(
            EffectTemplateLink.kind == kind,
            EffectTemplateLink.target_id == target_id,
        )
    )
    return result.rowcount or 0
