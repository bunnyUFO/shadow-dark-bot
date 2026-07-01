import logging
from pathlib import Path

import discord
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from discord.ext import commands

from shadowdark_bot.config import settings
from shadowdark_bot.db import ensure_sqlite_dir

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("shadowdark_bot")


def run_migrations() -> None:
    """Apply any pending Alembic migrations before the bot connects to Discord."""
    log.info("Running migrations...")
    ensure_sqlite_dir()
    repo_root = Path(__file__).resolve().parents[2]
    alembic_cfg = AlembicConfig(str(repo_root / "alembic.ini"))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
    alembic_command.upgrade(alembic_cfg, "head")
    log.info("Migrations applied.")


class ShadowDarkBot(commands.Bot):
    """Syncs slash commands to every guild it's in. Note: data is shared
    across all guilds — see roadmap for multi-tenant work."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self._synced_guilds: set[int] = set()

    async def setup_hook(self) -> None:
        await self.load_extension("shadowdark_bot.cogs.items_database")
        await self.load_extension("shadowdark_bot.cogs.guild_inventory")
        await self.load_extension("shadowdark_bot.cogs.magical_treasury")
        await self.load_extension("shadowdark_bot.cogs.guild_coffers")
        await self.load_extension("shadowdark_bot.cogs.player_characters")

    async def _sync_to_guild(self, guild: discord.Guild) -> None:
        if guild.id in self._synced_guilds:
            return
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d command(s) to guild %s (%s)", len(synced), guild.name, guild.id)
        self._synced_guilds.add(guild.id)

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        if not self.guilds:
            log.warning("Bot is not in any guild yet; commands will sync once invited.")
            return
        for guild in self.guilds:
            await self._sync_to_guild(guild)

    async def on_guild_join(self, guild: discord.Guild) -> None:
        log.info("Joined guild %s (%s); syncing commands.", guild.name, guild.id)
        await self._sync_to_guild(guild)


bot = ShadowDarkBot()


@bot.tree.command(name="ping", description="Smoke test - replies with pong")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("pong", ephemeral=True)


def main() -> None:
    run_migrations()
    bot.run(settings.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
