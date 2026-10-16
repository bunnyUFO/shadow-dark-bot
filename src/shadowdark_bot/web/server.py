"""Run the web apps inside the bot process, on the bot's event loop.

One process keeps a single SQLite writer and lets the public login check guild
membership with the bot's own Discord connection. Up to two apps run side by
side, each on its own port:

- local  (LOCAL_WEB_ENABLED): home network only, pick-a-character, no Discord.
- public (WEB_ENABLED): Discord login; what the reverse proxy and Activity use.
"""

import asyncio
import contextlib
import logging
import secrets
from collections.abc import AsyncIterator, Iterator

import discord
import uvicorn
from fastapi import FastAPI

from shadowdark_bot.config import Settings
from shadowdark_bot.services.guild import NameResolver
from shadowdark_bot.web.app import MembershipCheck, create_app
from shadowdark_bot.web.auth import DiscordOAuth, SessionSigner

log = logging.getLogger("shadowdark_bot.web")

READY_TIMEOUT_SECONDS = 15
LOCAL_SESSION_TTL_SECONDS = 30 * 24 * 3600  # it's your own network; stay picked


async def _allowed_members(
    bot: discord.Client, guild_ids: set[int], user_id: str
) -> AsyncIterator[discord.Member]:
    """The user as a member of each allowed guild they're in.

    The bot has no members intent, so the member cache is mostly empty; fall
    back to a REST lookup, which works for any guild the bot is in. Guilds the
    bot isn't in yield nothing."""
    uid = int(user_id)
    for gid in guild_ids:
        guild = bot.get_guild(gid)
        if guild is None:
            continue
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except discord.NotFound:
                continue
            except discord.HTTPException:
                log.exception("Member lookup failed for guild %s", gid)
                continue
        yield member


async def _bot_ready(bot: discord.Client) -> bool:
    try:
        await asyncio.wait_for(bot.wait_until_ready(), READY_TIMEOUT_SECONDS)
    except TimeoutError:
        log.warning("Login attempted before the bot was ready; denying.")
        return False
    return True


def bot_membership_check(bot: discord.Client, guild_ids: set[int]) -> MembershipCheck:
    """A MembershipCheck backed by the bot: is the user in any allowed guild?"""

    async def is_allowed_member(user_id: str) -> bool:
        if not await _bot_ready(bot):
            return False
        async for _ in _allowed_members(bot, guild_ids, user_id):
            return True
        return False

    return is_allowed_member


def bot_dm_role_check(
    bot: discord.Client, guild_ids: set[int], role_ids: set[int]
) -> MembershipCheck:
    """Does the user hold a Dungeon Master role in any allowed guild?"""

    async def has_dm_role(user_id: str) -> bool:
        if not await _bot_ready(bot):
            return False
        async for member in _allowed_members(bot, guild_ids, user_id):
            if {role.id for role in member.roles} & role_ids:
                return True
        return False

    return has_dm_role


def bot_name_resolver(bot: discord.Client, guild_ids: set[int]) -> NameResolver:
    """Display names from the bot's caches (no network): the member in an
    allowed guild first, then the plain user cache. Which users are cached
    depends on the Server Members intent; unknown users return None."""

    def resolve(user_id: str) -> str | None:
        try:
            uid = int(user_id)
        except ValueError:
            return None
        for gid in guild_ids:
            guild = bot.get_guild(gid)
            member = guild.get_member(uid) if guild is not None else None
            if member is not None:
                return member.display_name
        user = bot.get_user(uid)
        return user.display_name if user is not None else None

    return resolve


def build_public_app(bot: discord.Client, settings: Settings) -> FastAPI:
    return create_app(
        mode="discord",
        signer=SessionSigner(
            settings.SESSION_SECRET,
            ttl_seconds=settings.SESSION_TTL_HOURS * 3600,
            purpose="public",
        ),
        oauth=DiscordOAuth(settings.DISCORD_CLIENT_ID, settings.DISCORD_CLIENT_SECRET),
        is_allowed_member=bot_membership_check(bot, settings.allowed_guild_ids),
        public_base_url=settings.PUBLIC_BASE_URL,
        resolve_name=bot_name_resolver(bot, settings.allowed_guild_ids),
        dm_user_ids=settings.dm_user_ids,
        has_dm_role=(
            bot_dm_role_check(bot, settings.allowed_guild_ids, settings.dm_role_ids)
            if settings.dm_role_ids
            else None
        ),
    )


def build_local_app(dm_user_ids: frozenset[str] = frozenset()) -> FastAPI:
    # A fresh random key every start, never SESSION_SECRET: local sessions are
    # handed out without a login, so they must never be valid anywhere else.
    # (Restarting the bot just means picking your character again.)
    return create_app(
        mode="local",
        signer=SessionSigner(
            secrets.token_urlsafe(48),
            ttl_seconds=LOCAL_SESSION_TTL_SECONDS,
            purpose="local",
        ),
        dm_user_ids=dm_user_ids,
    )


class _EmbeddedServer(uvicorn.Server):
    """uvicorn without its own signal handling — discord.py's `bot.run()` owns
    SIGINT/SIGTERM and shuts us down via `WebServer.stop()`."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class WebServer:
    def __init__(
        self,
        app: FastAPI,
        *,
        name: str,
        host: str,
        port: int,
        forwarded_allow_ips: str | None = None,
    ) -> None:
        self.name = name
        config = uvicorn.Config(
            app,
            host=host,
            port=port,
            # Only the public app sits behind the reverse proxy. The local app
            # must see the real peer address (it refuses proxied requests).
            proxy_headers=forwarded_allow_ips is not None,
            forwarded_allow_ips=forwarded_allow_ips,
            log_config=None,  # use the bot's logging setup
        )
        self._server = _EmbeddedServer(config)
        self._task: asyncio.Task | None = None

    @classmethod
    def public(cls, bot: discord.Client, settings: Settings) -> "WebServer":
        return cls(
            build_public_app(bot, settings),
            name="public",
            host=settings.WEB_HOST,
            port=settings.WEB_PORT,
            forwarded_allow_ips=settings.FORWARDED_ALLOW_IPS,
        )

    @classmethod
    def local(cls, settings: Settings) -> "WebServer":
        return cls(
            build_local_app(settings.dm_user_ids),
            name="local",
            host=settings.LOCAL_WEB_HOST,
            port=settings.LOCAL_WEB_PORT,
        )

    def start(self) -> None:
        self._task = asyncio.create_task(self._serve(), name=f"web-{self.name}")

    async def _serve(self) -> None:
        """Serve until stopped. A web failure (e.g. the port is taken — uvicorn
        calls sys.exit) is logged and must not take the Discord bot down."""
        try:
            await self._server.serve()
        except SystemExit:
            log.error("%s web app failed to start; the bot keeps running.", self.name)
        except Exception:
            log.exception("%s web app crashed; the bot keeps running.", self.name)

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
        except TimeoutError:
            self._task.cancel()
