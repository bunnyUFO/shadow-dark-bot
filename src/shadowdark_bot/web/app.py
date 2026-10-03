"""FastAPI app: login and the character-sheet API, plus the static frontend.

Two auth modes share every sheet endpoint and differ only in how a session
starts:

- "discord" (public app): exchange a Discord OAuth code; the user must be in an
  allowed guild.
- "local" (home-network app): no Discord — pick a character from a list. Only
  safe on a trusted LAN, so this mode refuses requests that arrive through a
  reverse proxy or from a non-private address.

Collaborators are injected so tests can run the app without Discord. DB
endpoints are plain `def` functions — FastAPI runs them in a worker thread,
keeping SQLite calls off the event loop the Discord gateway shares.
"""

import ipaddress
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import select

from shadowdark_bot.db import session_scope
from shadowdark_bot.models import Item, PlayerCharacter, Spell
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
    CharacterSummaryOut,
    SpellOut,
    UserOut,
    catalog_item_out,
    character_sheet,
    character_summary,
    spell_out,
)

AuthMode = Literal["discord", "local"]

# Given a Discord user id, is that user in one of our allowed guilds?
MembershipCheck = Callable[[str], Awaitable[bool]]

STATIC_DIR = Path(__file__).parent / "static"

NOT_A_MEMBER = (
    "This sheet is only for members of our Discord servers. "
    "Ask the GM for an invite."
)
LOCAL_ONLY = "The local sheet is only available on the home network."

# Headers a reverse proxy adds. Seeing any of them in local mode means someone
# pointed the proxy at the local port — refuse rather than expose a no-login app.
_PROXY_HEADERS = ("x-forwarded-for", "x-forwarded-host", "x-real-ip", "forwarded")


class ExchangeIn(BaseModel):
    code: str
    # "web": the standalone redirect login; "activity": the Embedded App SDK.
    flow: Literal["web", "activity"] = "web"


class LocalLoginIn(BaseModel):
    user_id: str


class SessionOut(BaseModel):
    token: str
    expires_in: int
    user: UserOut
    # Only for the Activity flow: the SDK's authenticate() needs it.
    discord_access_token: str | None = None


class AuthConfigOut(BaseModel):
    mode: AuthMode
    client_id: str | None = None
    redirect_uri: str | None = None
    scopes: list[str] = []


class MeOut(BaseModel):
    user_id: str
    display_name: str
    has_character: bool


def _is_local_address(host: str | None) -> bool:
    if not host:
        return False
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


def create_app(
    *,
    mode: AuthMode,
    signer: SessionSigner,
    oauth: DiscordOAuth | None = None,
    is_allowed_member: MembershipCheck | None = None,
    public_base_url: str = "",
    static_dir: Path | None = STATIC_DIR,
) -> FastAPI:
    if mode == "discord" and (oauth is None or is_allowed_member is None):
        raise ValueError("discord mode needs oauth and is_allowed_member")

    app = FastAPI(
        title="Shadowdark character sheet",
        docs_url=None,  # no public schema browser
        redoc_url=None,
        openapi_url=None,
    )
    redirect_uri = f"{public_base_url.rstrip('/')}/auth/callback"

    if mode == "local":

        @app.middleware("http")
        async def _local_only(request: Request, call_next):
            proxied = any(h in request.headers for h in _PROXY_HEADERS)
            client = request.client.host if request.client else None
            if proxied or not _is_local_address(client):
                return JSONResponse(status_code=403, content={"detail": LOCAL_ONLY})
            return await call_next(request)

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        # Deliberately no X-Frame-Options: Discord must be able to iframe the app.
        return response

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
        """How the frontend should start a session (no secrets)."""
        if mode == "local":
            return AuthConfigOut(mode="local")
        return AuthConfigOut(
            mode="discord",
            client_id=oauth.client_id,
            redirect_uri=redirect_uri,
            scopes=list(SCOPES),
        )

    if mode == "discord":

        @app.post("/api/auth/exchange")
        async def exchange(body: ExchangeIn) -> SessionOut:
            try:
                access_token = await oauth.exchange_code(
                    body.code, redirect_uri if body.flow == "web" else None
                )
                discord_user = await oauth.fetch_user(access_token)
            except OAuthError as err:
                raise HTTPException(status_code=401, detail=str(err)) from err
            if not await is_allowed_member(discord_user.id):
                raise HTTPException(status_code=403, detail=NOT_A_MEMBER)
            return SessionOut(
                token=signer.issue(discord_user.id, discord_user.display_name),
                expires_in=signer.ttl_seconds,
                user=UserOut(
                    id=discord_user.id,
                    display_name=discord_user.display_name,
                    avatar_url=discord_user.avatar_url,
                ),
                discord_access_token=access_token if body.flow == "activity" else None,
            )

    if mode == "local":

        @app.get("/api/auth/local/characters")
        def local_characters() -> list[CharacterSummaryOut]:
            """Characters to pick from on the local login screen."""
            with session_scope() as session:
                return _summaries(session)

        @app.post("/api/auth/local")
        def local_login(body: LocalLoginIn) -> SessionOut:
            """Start a session as an existing character's owner — no password;
            the home network is the trust boundary."""
            with session_scope() as session:
                char = characters.load_character(session, body.user_id)
                if char is None:
                    raise CharacterNotFound("No character with that id.")
                name = char.name
            return SessionOut(
                token=signer.issue(body.user_id, name),
                expires_in=signer.ttl_seconds,
                user=UserOut(id=body.user_id, display_name=name),
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

    @app.get("/api/characters")
    def all_characters(user: User) -> list[CharacterSummaryOut]:
        """Everyone's characters (the party list)."""
        with session_scope() as session:
            return _summaries(session)

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

    @app.api_route("/api/{path:path}", methods=["GET", "POST", "PATCH", "DELETE"])
    def api_not_found(path: str) -> None:
        raise HTTPException(status_code=404, detail="Not found.")

    # ---------- frontend ----------

    if static_dir is not None and (static_dir / "index.html").is_file():
        index = static_dir / "index.html"
        app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            """Every non-API path serves the single-page app (it routes itself)."""
            return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return app


def _summaries(session) -> list[CharacterSummaryOut]:
    rows = session.scalars(select(PlayerCharacter).order_by(PlayerCharacter.name)).all()
    return [character_summary(c) for c in rows]
