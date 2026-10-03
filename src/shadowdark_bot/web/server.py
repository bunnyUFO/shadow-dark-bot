"""Run the web app inside the bot process, on the bot's event loop.

One process keeps a single SQLite writer and lets the login check guild
membership with the bot's own Discord connection.
"""

import asyncio
import contextlib
import logging
from collections.abc import Iterator

import discord
import uvicorn

from shadowdark_bot.config import Settings
from shadowdark_bot.web.app import MembershipCheck, create_app
from shadowdark_bot.web.auth import DiscordOAuth, SessionSigner

log = logging.getLogger("shadowdark_bot.web")

READY_TIMEOUT_SECONDS = 15


def bot_membership_check(bot: discord.Client, guild_ids: set[int]) -> MembershipCheck:
    """A MembershipCheck backed by the bot: is the user in any allowed guild?

    The bot has no members intent, so the member cache is mostly empty; fall
    back to a REST lookup, which works for any guild the bot is in. Guilds the
    bot isn't in can't grant access."""

    async def is_allowed_member(user_id: str) -> bool:
        try:
            await asyncio.wait_for(bot.wait_until_ready(), READY_TIMEOUT_SECONDS)
        except TimeoutError:
            log.warning("Login attempted before the bot was ready; denying.")
            return False
        uid = int(user_id)
        for gid in guild_ids:
            guild = bot.get_guild(gid)
            if guild is None:
                continue
            if guild.get_member(uid) is not None:
                return True
            try:
                await guild.fetch_member(uid)
                return True
            except discord.NotFound:
                continue
            except discord.HTTPException:
                log.exception("Membership lookup failed for guild %s", gid)
        return False

    return is_allowed_member


class _EmbeddedServer(uvicorn.Server):
    """uvicorn without its own signal handling — discord.py's `bot.run()` owns
    SIGINT/SIGTERM and shuts us down via `WebServer.stop()`."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class WebServer:
    def __init__(self, bot: discord.Client, settings: Settings) -> None:
        app = create_app(
            oauth=DiscordOAuth(settings.DISCORD_CLIENT_ID, settings.DISCORD_CLIENT_SECRET),
            signer=SessionSigner(
                settings.SESSION_SECRET, ttl_seconds=settings.SESSION_TTL_HOURS * 3600
            ),
            is_allowed_member=bot_membership_check(bot, settings.allowed_guild_ids),
            public_base_url=settings.PUBLIC_BASE_URL,
        )
        config = uvicorn.Config(
            app,
            host=settings.WEB_HOST,
            port=settings.WEB_PORT,
            proxy_headers=True,
            forwarded_allow_ips=settings.FORWARDED_ALLOW_IPS,
            log_config=None,  # use the bot's logging setup
        )
        self._server = _EmbeddedServer(config)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._serve(), name="web-server")

    async def _serve(self) -> None:
        """Serve until stopped. A web failure (e.g. the port is taken — uvicorn
        calls sys.exit) is logged and must not take the Discord bot down."""
        try:
            await self._server.serve()
        except SystemExit:
            log.error("Web server failed to start; the bot keeps running without it.")
        except Exception:
            log.exception("Web server crashed; the bot keeps running without it.")

    async def stop(self) -> None:
        if self._task is None or self._task.done():
            return
        self._server.should_exit = True
        try:
            await asyncio.wait_for(asyncio.shield(self._task), timeout=10)
        except TimeoutError:
            self._task.cancel()
