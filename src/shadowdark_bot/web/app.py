"""FastAPI app: Discord login and the character-sheet API.

Collaborators (Discord OAuth, the session signer, the guild-membership check)
are injected so tests can run the app without Discord. DB endpoints are plain
`def` functions — FastAPI runs them in a worker thread, keeping SQLite calls off
the event loop the Discord gateway shares.
"""

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import Item, Spell
from shadowdark_bot.services import characters
from shadowdark_bot.services.characters import CharacterError, CharacterNotFound
from shadowdark_bot.web.auth import (
    SCOPES,
    DiscordOAuth,
    OAuthError,
    SessionSigner,
    SessionUser,
)
from shadowdark_bot.web.schemas import (
    CatalogItemOut,
    CharacterSheetOut,
    SpellOut,
    UserOut,
    catalog_item_out,
    character_sheet,
    spell_out,
)

# Given a Discord user id, is that user in one of our allowed guilds?
MembershipCheck = Callable[[str], Awaitable[bool]]

NOT_A_MEMBER = (
    "This sheet is only for members of our Discord servers. "
    "Ask the GM for an invite."
)


class ExchangeIn(BaseModel):
    code: str
    # "web": the standalone redirect login; "activity": the Embedded App SDK.
    flow: Literal["web", "activity"] = "web"


class ExchangeOut(BaseModel):
    token: str
    expires_in: int
    user: UserOut
    # Only for the Activity flow: the SDK's authenticate() needs it.
    discord_access_token: str | None = None


class AuthConfigOut(BaseModel):
    client_id: str
    redirect_uri: str
    scopes: list[str]


class MeOut(BaseModel):
    user_id: str
    display_name: str
    has_character: bool


def create_app(
    *,
    oauth: DiscordOAuth,
    signer: SessionSigner,
    is_allowed_member: MembershipCheck,
    public_base_url: str,
) -> FastAPI:
    app = FastAPI(
        title="Shadowdark character sheet",
        docs_url=None,  # no public schema browser
        redoc_url=None,
        openapi_url=None,
    )
    redirect_uri = f"{public_base_url.rstrip('/')}/auth/callback"

    @app.exception_handler(CharacterNotFound)
    async def _not_found(_: Request, err: CharacterNotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(err)})

    @app.exception_handler(CharacterError)
    async def _rule_failed(_: Request, err: CharacterError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(err)})

    def current_user(request: Request) -> SessionUser:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        user = signer.verify(token) if scheme.lower() == "bearer" and token else None
        if user is None:
            raise HTTPException(
                status_code=401,
                detail="Not logged in.",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    User = Annotated[SessionUser, Depends(current_user)]

    # ---------- public ----------

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    @app.get("/api/auth/config")
    def auth_config() -> AuthConfigOut:
        """What the frontend needs to start a login (no secrets)."""
        return AuthConfigOut(
            client_id=oauth.client_id, redirect_uri=redirect_uri, scopes=list(SCOPES)
        )

    @app.post("/api/auth/exchange")
    async def exchange(body: ExchangeIn) -> ExchangeOut:
        try:
            access_token = await oauth.exchange_code(
                body.code, redirect_uri if body.flow == "web" else None
            )
            discord_user = await oauth.fetch_user(access_token)
        except OAuthError as err:
            raise HTTPException(status_code=401, detail=str(err)) from err
        if not await is_allowed_member(discord_user.id):
            raise HTTPException(status_code=403, detail=NOT_A_MEMBER)
        return ExchangeOut(
            token=signer.issue(discord_user),
            expires_in=signer.ttl_seconds,
            user=UserOut(
                id=discord_user.id,
                display_name=discord_user.display_name,
                avatar_url=discord_user.avatar_url,
            ),
            discord_access_token=access_token if body.flow == "activity" else None,
        )

    # ---------- signed in ----------

    @app.get("/api/me")
    def me(user: User) -> MeOut:
        with session_scope() as session:
            has_character = characters.load_character(session, user.user_id) is not None
        return MeOut(
            user_id=user.user_id,
            display_name=user.display_name,
            has_character=has_character,
        )

    @app.get("/api/character")
    def my_character(user: User) -> CharacterSheetOut:
        with session_scope() as session:
            char = characters.require_character(session, user.user_id)
            return character_sheet(session, char, include_stash=True)

    @app.get("/api/characters/{user_id}")
    def other_character(user_id: str, user: User) -> CharacterSheetOut:
        """Read-only view of any player's sheet — held items only, never the
        stash (same as `/character show`)."""
        with session_scope() as session:
            char = characters.load_character(session, user_id)
            if char is None:
                raise CharacterNotFound("That player doesn't have a character yet.")
            return character_sheet(session, char, include_stash=False)

    @app.get("/api/spells")
    def spells(
        user: User,
        spell_class: Annotated[
            Literal["wizard", "priest"] | None, Query(alias="class")
        ] = None,
        tier: Annotated[int | None, Query(ge=1, le=10)] = None,
    ) -> list[SpellOut]:
        """The built-in spell reference, optionally filtered."""
        stmt = select(Spell).order_by(Spell.tier, Spell.name)
        if spell_class is not None:
            stmt = stmt.where(Spell.classes.contains(spell_class))
        if tier is not None:
            stmt = stmt.where(Spell.tier == tier)
        with session_scope() as session:
            return [spell_out(sp) for sp in session.scalars(stmt).all()]

    @app.get("/api/items")
    def items(
        user: User,
        q: Annotated[str, Query(max_length=100)] = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 25,
    ) -> list[CatalogItemOut]:
        """Catalog items matching `q` (for the carry picker)."""
        stmt = select(Item).order_by(Item.name).limit(limit)
        if q.strip():
            stmt = stmt.where(Item.name.ilike(f"%{q.strip()}%"))
        with session_scope() as session:
            return [catalog_item_out(item) for item in session.scalars(stmt).all()]

    return app
