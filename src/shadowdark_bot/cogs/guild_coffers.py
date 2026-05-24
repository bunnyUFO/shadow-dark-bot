import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import Session

from shadowdark_bot.currency import format_cp, parse_to_cp
from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import build_coffer_change_embed, build_coffer_show_embed
from shadowdark_bot.models import Coffer, Item
from shadowdark_bot.sharing import ShareableView

log = logging.getLogger("shadowdark_bot.coffers")


class GuildCoffers(commands.Cog):
    """Commands for managing the shared guild coffers."""

    coffers = app_commands.Group(
        name="coffers", description="Manage the shared guild coffers"
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @coffers.command(name="show", description="Show the current coffer balance")
    async def show(self, interaction: discord.Interaction) -> None:
        with session_scope() as session:
            coffer = _get_or_create_coffer(session)
            embed = build_coffer_show_embed(coffer.balance_cp)
            await interaction.response.send_message(
                embed=embed, view=ShareableView(), ephemeral=True
            )

    @coffers.command(name="add", description="Add funds to the coffers")
    @app_commands.describe(
        gp="Gold pieces to add",
        sp="Silver pieces to add",
        cp="Copper pieces to add",
        reason="Optional reason (where the money came from)",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        gp: int | None = None,
        sp: int | None = None,
        cp: int | None = None,
        reason: str | None = None,
    ) -> None:
        delta_cp = parse_to_cp(gp, sp, cp)
        if delta_cp is None or delta_cp <= 0:
            await interaction.response.send_message(
                "**Failed to add funds.**\nProvide a positive amount in gp, sp, or cp.",
                ephemeral=True,
            )
            return

        clean_reason = reason.strip() if reason else None
        with session_scope() as session:
            coffer = _get_or_create_coffer(session)
            coffer.balance_cp += delta_cp
            session.flush()
            embed = build_coffer_change_embed(
                action="added",
                delta_cp=delta_cp,
                new_balance_cp=coffer.balance_cp,
                reason=clean_reason,
            )
            await interaction.response.send_message(embed=embed)

    @coffers.command(name="subtract", description="Subtract funds from the coffers")
    @app_commands.describe(
        gp="Gold pieces to subtract",
        sp="Silver pieces to subtract",
        cp="Copper pieces to subtract",
        reason="Optional reason (what the money was spent on)",
    )
    async def subtract(
        self,
        interaction: discord.Interaction,
        gp: int | None = None,
        sp: int | None = None,
        cp: int | None = None,
        reason: str | None = None,
    ) -> None:
        delta_cp = parse_to_cp(gp, sp, cp)
        if delta_cp is None or delta_cp <= 0:
            await interaction.response.send_message(
                "**Failed to subtract funds.**\nProvide a positive amount in gp, sp, or cp.",
                ephemeral=True,
            )
            return

        clean_reason = reason.strip() if reason else None
        with session_scope() as session:
            coffer = _get_or_create_coffer(session)
            if coffer.balance_cp < delta_cp:
                deficit = delta_cp - coffer.balance_cp
                await interaction.response.send_message(
                    f"**Failed to subtract funds.**\n"
                    f"Balance is {_fmt(coffer.balance_cp)}; "
                    f"can't subtract {_fmt(delta_cp)}. "
                    f"Short by {_fmt(deficit)}.",
                    ephemeral=True,
                )
                return
            coffer.balance_cp -= delta_cp
            session.flush()
            embed = build_coffer_change_embed(
                action="subtracted",
                delta_cp=delta_cp,
                new_balance_cp=coffer.balance_cp,
                reason=clean_reason,
            )
            await interaction.response.send_message(embed=embed)

    @coffers.command(name="buy", description="Buy a catalog item using coffer funds")
    @app_commands.describe(item="Catalog item to buy (must have a value set)")
    async def buy(
        self,
        interaction: discord.Interaction,
        item: str,
    ) -> None:
        clean_item = item.strip()
        failure = f"**Failed to buy {clean_item}.**"
        with session_scope() as session:
            cat_item = session.scalar(select(Item).where(Item.name == clean_item))
            if cat_item is None:
                await interaction.response.send_message(
                    f"{failure}\nNo catalog entry named **{clean_item}**.",
                    ephemeral=True,
                )
                return
            if cat_item.value_cp is None:
                await interaction.response.send_message(
                    f"{failure}\n**{clean_item}** has no value set. "
                    "Use `/items edit` to set one first.",
                    ephemeral=True,
                )
                return
            price_cp = cat_item.value_cp
            if price_cp <= 0:
                await interaction.response.send_message(
                    f"{failure}\n**{clean_item}** has a value of 0 — nothing to deduct.",
                    ephemeral=True,
                )
                return

            coffer = _get_or_create_coffer(session)
            if coffer.balance_cp < price_cp:
                deficit = price_cp - coffer.balance_cp
                await interaction.response.send_message(
                    f"{failure}\n"
                    f"Coffer balance is {_fmt(coffer.balance_cp)}; "
                    f"price is {_fmt(price_cp)}. Short by {_fmt(deficit)}.",
                    ephemeral=True,
                )
                return

            coffer.balance_cp -= price_cp
            session.flush()
            embed = build_coffer_change_embed(
                action="bought",
                delta_cp=price_cp,
                new_balance_cp=coffer.balance_cp,
                item_name=cat_item.name,
            )
            await interaction.response.send_message(embed=embed)

    @buy.autocomplete("item")
    async def _ac_buy_item(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        with session_scope() as session:
            stmt = (
                select(Item.name)
                .where(
                    Item.value_cp.is_not(None),
                    Item.name.ilike(f"%{current}%"),
                )
                .order_by(Item.name)
                .limit(25)
            )
            names = list(session.scalars(stmt).all())
        return [app_commands.Choice(name=n, value=n) for n in names]


def _get_or_create_coffer(session: Session) -> Coffer:
    """Auto-create the singleton coffer row on first use."""
    coffer = session.scalars(select(Coffer).limit(1)).first()
    if coffer is None:
        coffer = Coffer(balance_cp=0)
        session.add(coffer)
        session.flush()
    return coffer


def _fmt(cp: int) -> str:
    return format_cp(cp) or "0cp"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildCoffers(bot))
