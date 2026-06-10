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
from shadowdark_bot.sharing import ShareButton, ShareableView

VIEW_TIMEOUT_SECONDS = 300

log = logging.getLogger("shadowdark_bot.coffers")


class GuildCoffers(commands.Cog):
    """Commands for managing the shared guild coffers."""

    coffers = app_commands.Group(
        name="coffers", description="Manage the shared guild coffers"
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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
            await interaction.response.send_message(
                embed=embed, view=ShareableView(), ephemeral=True
            )

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
            await interaction.response.send_message(
                embed=embed, view=ShareableView(), ephemeral=True
            )

    @coffers.command(
        name="browse",
        description="View the coffer balance and buy catalog items",
    )
    async def browse(self, interaction: discord.Interaction) -> None:
        embed, view = _build_coffers_browse_payload()
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )


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


# ---------- Browse view + buy modal ----------


def _build_coffers_browse_payload() -> tuple[discord.Embed, "CoffersBrowseView"]:
    """Embed + view for /coffers browse: balance plus a buyable-items dropdown."""
    with session_scope() as session:
        coffer = _get_or_create_coffer(session)
        balance_cp = coffer.balance_cp
        items = list(
            session.scalars(
                select(Item)
                .where(Item.value_cp.is_not(None), Item.value_cp > 0)
                .order_by(Item.name)
                .limit(25)
            ).all()
        )
        item_data = [(it.name, it.value_cp) for it in items]
    embed = build_coffer_show_embed(balance_cp)
    if not item_data:
        embed.description = (
            f"{embed.description}\n\n_No catalog items have a value set yet — "
            "use `/items edit` to add prices._"
        )
    return embed, CoffersBrowseView(item_data)


class BuyItemSelect(discord.ui.Select):
    def __init__(self, item_data: list[tuple[str, int]]) -> None:
        options = []
        for name, price_cp in item_data[:25]:
            price_str = format_cp(price_cp) or "—"
            label = f"{name} — {price_str}"[:100]
            options.append(
                discord.SelectOption(label=label, value=name[:100])
            )
        super().__init__(
            placeholder="Buy an item…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.prices = dict(item_data)

    async def callback(self, interaction: discord.Interaction) -> None:
        item_name = self.values[0]
        price_cp = self.prices.get(item_name)
        if price_cp is None:
            await interaction.response.send_message(
                f"**{item_name}** is no longer for sale.", ephemeral=True
            )
            return
        await interaction.response.send_modal(
            BuyQuantityModal(item_name, price_cp)
        )


class CoffersBrowseView(discord.ui.View):
    def __init__(self, item_data: list[tuple[str, int]]) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        if item_data:
            self.add_item(BuyItemSelect(item_data))
        self.add_item(ShareButton(row=4))


class BuyQuantityModal(discord.ui.Modal):
    def __init__(self, item_name: str, price_cp: int) -> None:
        super().__init__(title=f"Buy {item_name}"[:45])
        self.item_name = item_name
        price_str = format_cp(price_cp) or "—"
        self._qty = discord.ui.TextInput(
            label=f"Quantity (price: {price_str} each)",
            required=True,
            default="1",
            max_length=8,
        )
        self.add_item(self._qty)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            quantity = int(str(self._qty.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "**Failed to buy.**\nQuantity must be a whole number.",
                ephemeral=True,
            )
            return
        await _do_buy(
            interaction,
            item_name=self.item_name,
            quantity=quantity,
            edit_view=True,
        )


async def _do_buy(
    interaction: discord.Interaction,
    *,
    item_name: str,
    quantity: int,
    edit_view: bool = False,
) -> None:
    clean_item = item_name.strip()
    label = f"{quantity}× {clean_item}" if quantity > 1 else clean_item
    failure = f"**Failed to buy {label}.**"

    if quantity < 1:
        await interaction.response.send_message(
            f"{failure}\nQuantity must be ≥ 1.", ephemeral=True
        )
        return

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

        total_cp = price_cp * quantity
        coffer = _get_or_create_coffer(session)
        if coffer.balance_cp < total_cp:
            deficit = total_cp - coffer.balance_cp
            await interaction.response.send_message(
                f"{failure}\n"
                f"Coffer balance is {_fmt(coffer.balance_cp)}; "
                f"price is {_fmt(total_cp)} ({quantity} × {_fmt(price_cp)}). "
                f"Short by {_fmt(deficit)}.",
                ephemeral=True,
            )
            return

        coffer.balance_cp -= total_cp
        session.flush()
        display_name = (
            f"{quantity}× {cat_item.name}" if quantity > 1 else cat_item.name
        )
        embed = build_coffer_change_embed(
            action="bought",
            delta_cp=total_cp,
            new_balance_cp=coffer.balance_cp,
            item_name=display_name,
        )

    if edit_view:
        b_embed, b_view = _build_coffers_browse_payload()
        await interaction.response.edit_message(embed=b_embed, view=b_view)
        await interaction.followup.send(
            embed=embed, view=ShareableView(), ephemeral=True
        )
    else:
        await interaction.response.send_message(
            embed=embed, view=ShareableView(), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildCoffers(bot))
