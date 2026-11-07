"""Creature bestiary endpoints. Any signed-in user may read; only a Dungeon
Master may add, edit and delete (creatures and their encounters).

Registered by `create_app` (above its /api catch-all). Rules live in
`services.creatures`; this module only shapes JSON. Input stat fields accept
an int, a numeric string or blank/null, so the service words every error.
"""

import datetime as dt
from typing import Annotated

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import Creature, CreatureEncounter
from shadowdark_bot.services import creatures

_Num = int | str | None


class CreatureIn(BaseModel):
    """Create body. PATCH uses the same shape, applying only the fields sent."""

    name: str | None = None
    title: str | None = None
    tags: list[str] | str | None = None
    kind: str | None = None
    type_id: _Num = None
    level: _Num = None
    ac: _Num = None
    hp: _Num = None
    attacks: str | None = None
    movement: str | None = None
    str_mod: _Num = None
    dex_mod: _Num = None
    con_mod: _Num = None
    int_mod: _Num = None
    wis_mod: _Num = None
    cha_mod: _Num = None
    alignment: str | None = None
    special_abilities: str | None = None
    description: str | None = None
    notes: str | None = None


class EncounterIn(BaseModel):
    details: str | None = None


class EncounterOut(BaseModel):
    id: int
    details: str | None
    created_by: str | None
    created_at: dt.datetime


class CreatureSummaryOut(BaseModel):
    id: int
    name: str
    title: str | None
    kind: str
    tags: list[str]
    type_id: int | None
    # Effective values: the creature's own, else its Type's.
    level: int | None
    ac: int | None
    hp: int | None
    alignment: str | None
    encounter_count: int


class TypeRef(BaseModel):
    id: int
    name: str


class CreatureOut(BaseModel):
    id: int
    name: str
    title: str | None
    kind: str
    tags: list[str]
    type: TypeRef | None
    level: int | None
    ac: int | None
    hp: int | None
    attacks: str | None
    movement: str | None
    str_mod: int | None
    dex_mod: int | None
    con_mod: int | None
    int_mod: int | None
    wis_mod: int | None
    cha_mod: int | None
    alignment: str | None
    special_abilities: str | None
    description: str | None
    notes: str | None
    # Values shown from the Type where this creature's own are blank.
    inherited: dict[str, int | str]
    created_by: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    encounters: list[EncounterOut]  # newest first


def _encounter_out(e: CreatureEncounter) -> EncounterOut:
    return EncounterOut(
        id=e.id, details=e.details, created_by=e.created_by, created_at=e.created_at
    )


_PLAIN = (
    "id", "name", "title", "kind", "level", "ac", "hp", "attacks", "movement",
    "str_mod", "dex_mod", "con_mod", "int_mod", "wis_mod", "cha_mod", "alignment",
    "special_abilities", "description", "notes", "created_by", "created_at",
    "updated_at",
)


def _creature_out(session: Session, creature_id: int) -> CreatureOut:
    c = creatures.get_creature(session, creature_id)
    parent = session.get(Creature, c.type_id) if c.type_id else None
    data = {name: getattr(c, name) for name in _PLAIN}
    return CreatureOut(
        **data,
        tags=c.tag_list(),
        type=TypeRef(id=parent.id, name=parent.name) if parent else None,
        inherited=creatures.inherited(session, c),
        encounters=[_encounter_out(e) for e in c.encounters],
    )


def _read(creature_id: int) -> CreatureOut:
    # A fresh session so the response reflects what the write just committed.
    with session_scope() as session:
        return _creature_out(session, creature_id)


def register(app: FastAPI, User, DungeonMaster) -> None:

    @app.get("/api/creatures")
    def list_creatures(
        user: User, q: Annotated[str, Query(max_length=100)] = ""
    ) -> list[CreatureSummaryOut]:
        with session_scope() as session:
            rows = creatures.list_creatures(session, q)
            # The bestiary is small: one lookup of Types serves every row.
            types = {
                c.id: c for c in session.scalars(
                    select(Creature).where(Creature.kind == "type")
                )
            }
            out = []
            for c, n in rows:
                parent = types.get(c.type_id) if c.kind == "named" else None

                def effective(field, c=c, parent=parent):
                    own = getattr(c, field)
                    if own is None and parent is not None:
                        return getattr(parent, field)
                    return own

                out.append(CreatureSummaryOut(
                    id=c.id, name=c.name, title=c.title, kind=c.kind,
                    tags=c.tag_list(), type_id=c.type_id,
                    level=effective("level"), ac=effective("ac"), hp=effective("hp"),
                    alignment=effective("alignment"), encounter_count=n,
                ))
            return out

    @app.get("/api/creatures/tags")
    def creature_tags(user: User) -> list[str]:
        with session_scope() as session:
            return creatures.tag_suggestions(session)

    @app.post("/api/creatures")
    def create_creature(body: CreatureIn, user: DungeonMaster) -> CreatureOut:
        with session_scope() as session:
            creature = creatures.create_creature(
                session, user.user_id, **body.model_dump()
            )
            creature_id = creature.id
        return _read(creature_id)

    @app.get("/api/creatures/{creature_id}")
    def get_creature(creature_id: int, user: User) -> CreatureOut:
        return _read(creature_id)

    @app.patch("/api/creatures/{creature_id}")
    def update_creature(creature_id: int, body: CreatureIn, user: DungeonMaster) -> CreatureOut:
        with session_scope() as session:
            creatures.update_creature(
                session, creature_id, **body.model_dump(exclude_unset=True)
            )
        return _read(creature_id)

    @app.delete("/api/creatures/{creature_id}")
    def delete_creature(creature_id: int, user: DungeonMaster) -> dict:
        with session_scope() as session:
            name = creatures.delete_creature(session, creature_id)
        return {"deleted": name}

    @app.post("/api/creatures/{creature_id}/encounters")
    def add_encounter(creature_id: int, body: EncounterIn, user: DungeonMaster) -> CreatureOut:
        with session_scope() as session:
            creatures.add_encounter(
                session, user.user_id, creature_id, body.model_dump()
            )
        return _read(creature_id)

    @app.patch("/api/creatures/{creature_id}/encounters/{encounter_id}")
    def update_encounter(
        creature_id: int, encounter_id: int, body: EncounterIn, user: DungeonMaster
    ) -> CreatureOut:
        with session_scope() as session:
            creatures.update_encounter(
                session, creature_id, encounter_id,
                body.model_dump(exclude_unset=True),
            )
        return _read(creature_id)

    @app.delete("/api/creatures/{creature_id}/encounters/{encounter_id}")
    def delete_encounter(creature_id: int, encounter_id: int, user: DungeonMaster) -> CreatureOut:
        with session_scope() as session:
            creatures.delete_encounter(session, creature_id, encounter_id)
        return _read(creature_id)
