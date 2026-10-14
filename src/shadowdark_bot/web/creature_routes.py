"""Creature bestiary endpoints. Any signed-in user may add, edit and delete.

Registered by `create_app` (above its /api catch-all). Rules live in
`services.creatures`; this module only shapes JSON. Input stat fields accept
an int, a numeric string or blank/null, so the service words every error.
"""

import datetime as dt
from typing import Annotated

from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import Creature, CreatureEncounter
from shadowdark_bot.services import creatures

_Num = int | str | None


class CreatureIn(BaseModel):
    """Create body. PATCH uses the same shape, applying only the fields sent."""

    name: str | None = None
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
    date: str | None = None  # YYYY-MM-DD; blank means today
    session: str | None = None
    place: str | None = None
    outcome: str | None = None


class EncounterOut(BaseModel):
    id: int
    date: dt.date
    session: str | None
    place: str | None
    outcome: str | None
    created_by: str | None


class CreatureSummaryOut(BaseModel):
    id: int
    name: str
    level: int | None
    ac: int | None
    hp: int | None
    alignment: str | None
    encounter_count: int
    last_seen: dt.date | None


class CreatureOut(BaseModel):
    id: int
    name: str
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
    created_by: str | None
    created_at: dt.datetime
    updated_at: dt.datetime
    encounters: list[EncounterOut]  # newest first


def _encounter_out(e: CreatureEncounter) -> EncounterOut:
    return EncounterOut(
        id=e.id, date=e.date, session=e.session, place=e.place,
        outcome=e.outcome, created_by=e.created_by,
    )


def _creature_out(session: Session, creature_id: int) -> CreatureOut:
    c = creatures.get_creature(session, creature_id)
    data = {col.name: getattr(c, col.name) for col in Creature.__table__.columns}
    return CreatureOut(**data, encounters=[_encounter_out(e) for e in c.encounters])


def _read(creature_id: int) -> CreatureOut:
    # A fresh session so the response reflects what the write just committed.
    with session_scope() as session:
        return _creature_out(session, creature_id)


def register(app: FastAPI, User) -> None:

    @app.get("/api/creatures")
    def list_creatures(
        user: User, q: Annotated[str, Query(max_length=100)] = ""
    ) -> list[CreatureSummaryOut]:
        with session_scope() as session:
            return [
                CreatureSummaryOut(
                    id=c.id, name=c.name, level=c.level, ac=c.ac, hp=c.hp,
                    alignment=c.alignment, encounter_count=n, last_seen=last,
                )
                for c, n, last in creatures.list_creatures(session, q)
            ]

    @app.post("/api/creatures")
    def create_creature(body: CreatureIn, user: User) -> CreatureOut:
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
    def update_creature(creature_id: int, body: CreatureIn, user: User) -> CreatureOut:
        with session_scope() as session:
            creatures.update_creature(
                session, creature_id, **body.model_dump(exclude_unset=True)
            )
        return _read(creature_id)

    @app.delete("/api/creatures/{creature_id}")
    def delete_creature(creature_id: int, user: User) -> dict:
        with session_scope() as session:
            name = creatures.delete_creature(session, creature_id)
        return {"deleted": name}

    @app.post("/api/creatures/{creature_id}/encounters")
    def add_encounter(creature_id: int, body: EncounterIn, user: User) -> CreatureOut:
        with session_scope() as session:
            creatures.add_encounter(
                session, user.user_id, creature_id, body.model_dump()
            )
        return _read(creature_id)

    @app.patch("/api/creatures/{creature_id}/encounters/{encounter_id}")
    def update_encounter(
        creature_id: int, encounter_id: int, body: EncounterIn, user: User
    ) -> CreatureOut:
        with session_scope() as session:
            creatures.update_encounter(
                session, creature_id, encounter_id,
                body.model_dump(exclude_unset=True),
            )
        return _read(creature_id)

    @app.delete("/api/creatures/{creature_id}/encounters/{encounter_id}")
    def delete_encounter(creature_id: int, encounter_id: int, user: User) -> CreatureOut:
        with session_scope() as session:
            creatures.delete_encounter(session, creature_id, encounter_id)
        return _read(creature_id)
