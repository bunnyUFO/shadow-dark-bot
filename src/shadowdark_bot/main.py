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
    """Single-guild bot: auto-discovers its guild from `self.guilds` on connect."""

    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(command_prefix="!", intents=intents)
        self._synced = False

    async def setup_hook(self) -> None:
        await self.load_extension("shadowdark_bot.cogs.items_database")
        await self.load_extension("shadowdark_bot.cogs.guild_inventory")
        await self.load_extension("shadowdark_bot.cogs.magical_treasury")
        await self.load_extension("shadowdark_bot.cogs.guild_coffers")

    async def on_ready(self) -> None:
        log.info("Logged in as %s (id=%s)", self.user, self.user.id if self.user else "?")
        if self._synced:
            return
        if not self.guilds:
            log.warning("Bot is not in any guild yet; commands will sync once invited.")
            return
        if len(self.guilds) > 1:
            log.warning(
                "Bot is in %d guilds; this build assumes a single guild. Using %s (%s).",
                len(self.guilds),
                self.guilds[0].name,
                self.guilds[0].id,
            )
        guild = self.guilds[0]
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d command(s) to guild %s (%s)", len(synced), guild.name, guild.id)
        self._synced = True

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self._synced:
            log.warning(
                "Joined additional guild %s (%s); single-guild build will not sync to it.",
                guild.name,
                guild.id,
            )
            return
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d command(s) on guild join: %s (%s)", len(synced), guild.name, guild.id)
        self._synced = True


bot = ShadowDarkBot()


@bot.tree.command(name="ping", description="Smoke test - replies with pong")
async def ping(interaction: discord.Interaction) -> None:
    await interaction.response.send_message("pong", ephemeral=True)


def main() -> None:
    run_migrations()
    bot.run(settings.DISCORD_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
