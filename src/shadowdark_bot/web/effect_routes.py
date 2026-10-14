"""Effects library endpoints (/api/effects). Any signed-in user may add, edit,
delete and link.

Registered by `create_app` (above its /api catch-all). Rules live in
`services.effects`; this module only shapes JSON.
"""

import datetime as dt
from typing import Annotated

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import EffectTemplate
from shadowdark_bot.services import effects


class EffectIn(BaseModel):
    """Create body. PATCH uses the same shape, applying only the fields sent."""

    name: str | None = None
    description: str | None = None
    max_rounds: int | None = None
    bonuses: dict[str, str | None] | None = None


class ApplyIn(BaseModel):
    target_user_id: str | None = None  # omitted = the caller's own character


class ApplyOut(BaseModel):
    target_user_id: str
    target_name: str
    effect_name: str


class LinkIn(BaseModel):
    kind: str
    target_id: int


class LinkOut(BaseModel):
    kind: str
    target_id: int
    target_name: str


class EffectOut(BaseModel):
    id: int
    name: str
    description: str | None
    max_rounds: int | None
    bonuses: dict[str, str]
    created_by: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    links: list[LinkOut]


def effect_out(session: Session, template: EffectTemplate) -> EffectOut:
    return effect_outs(session, [template])[0]


def effect_outs(session: Session, templates: list[EffectTemplate]) -> list[EffectOut]:
    links = [link for t in templates for link in t.links]
    names = effects.target_names(session, links)
    return [
        EffectOut(
            id=t.id,
            name=t.name,
            description=t.description,
            max_rounds=t.max_rounds,
            bonuses=t.bonus_map(),
            created_by=t.created_by,
            created_at=t.created_at,
            updated_at=t.updated_at,
            links=[
                LinkOut(kind=l.kind, target_id=l.target_id, target_name=names[(l.kind, l.target_id)])
                for l in t.links
                if (l.kind, l.target_id) in names
            ],
        )
        for t in templates
    ]


def _read(template_id: int) -> EffectOut:
    # A fresh session so the response reflects what the write just committed.
    with session_scope() as session:
        return effect_out(session, effects.get_template(session, template_id))


def register(app: FastAPI, User) -> None:

    @app.get("/api/effects")
    def list_effects(
        user: User,
        q: Annotated[str, Query(max_length=100)] = "",
        kind: Annotated[str | None, Query(max_length=20)] = None,
        target_id: int | None = None,
    ) -> list[EffectOut]:
        with session_scope() as session:
            return effect_outs(session, effects.list_templates(session, q, kind, target_id))

    @app.post("/api/effects")
    def create_effect(body: EffectIn, user: User) -> EffectOut:
        with session_scope() as session:
            template = effects.create_template(
                session, user.user_id, **body.model_dump()
            )
            template_id = template.id
        return _read(template_id)

    @app.get("/api/effects/{template_id}")
    def get_effect(template_id: int, user: User) -> EffectOut:
        return _read(template_id)

    @app.patch("/api/effects/{template_id}")
    def update_effect(template_id: int, body: EffectIn, user: User) -> EffectOut:
        with session_scope() as session:
            effects.update_template(
                session, template_id, **body.model_dump(exclude_unset=True)
            )
        return _read(template_id)

    @app.delete("/api/effects/{template_id}")
    def delete_effect(template_id: int, user: User) -> dict:
        with session_scope() as session:
            name = effects.delete_template(session, template_id)
        return {"deleted": name}

    @app.post("/api/effects/{template_id}/apply")
    def apply_effect(template_id: int, body: ApplyIn, user: User) -> ApplyOut:
        """Add a copy of this effect to a character's sheet (yours by default).
        The only edit allowed on another player's sheet, and only an add."""
        target = body.target_user_id or user.user_id
        with session_scope() as session:
            char, eff = effects.apply_template(session, template_id, target)
            return ApplyOut(
                target_user_id=target, target_name=char.name, effect_name=eff.name
            )

    @app.post("/api/effects/{template_id}/links")
    def add_link(template_id: int, body: LinkIn, user: User) -> EffectOut:
        with session_scope() as session:
            effects.add_link(session, template_id, body.kind, body.target_id)
        return _read(template_id)

    @app.delete("/api/effects/{template_id}/links/{kind}/{target_id}")
    def remove_link(template_id: int, kind: str, target_id: int, user: User) -> EffectOut:
        with session_scope() as session:
            effects.remove_link(session, template_id, kind, target_id)
        return _read(template_id)
