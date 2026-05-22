import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from shadowdark_bot.currency import parse_to_cp
from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import build_item_embed
from shadowdark_bot.models import InventoryEntry, Item, TreasuryEntry

log = logging.getLogger("shadowdark_bot.items")

MAX_ITEMS_PER_GROUP = 25


class ItemsDatabase(commands.Cog):
    """Commands for managing the item catalog."""

    items = app_commands.Group(name="items", description="Manage the guild item catalog")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @items.command(name="add", description="Add a new item to the catalog")
    @app_commands.describe(
        name="Item name (must be unique)",
        magical="Magical items live in the treasury (borrow-only). Non-magical go in inventory.",
        description="Optional description",
        gear_slots="Number of gear slots (Shadow Dark)",
        bundle_size="How many fit in one gear slot (e.g., 20 for arrows). Defaults to 1.",
        gp="Value in gold pieces (1 gp = 10 sp = 100 cp)",
        sp="Value in silver pieces",
        cp="Value in copper pieces",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        magical: bool,
        description: str | None = None,
        gear_slots: float | None = None,
        bundle_size: int | None = None,
        gp: int | None = None,
        sp: int | None = None,
        cp: int | None = None,
    ) -> None:
        clean_name = name.strip()
        if not clean_name:
            await interaction.response.send_message("Name cannot be empty.", ephemeral=True)
            return
        if bundle_size is not None and bundle_size < 1:
            await interaction.response.send_message(
                "Bundle size must be ≥ 1.", ephemeral=True
            )
            return

        value_cp = parse_to_cp(gp, sp, cp)

        with session_scope() as session:
            existing = session.scalar(select(Item).where(Item.name == clean_name))
            if existing is not None:
                await interaction.response.send_message(
                    f"An item named **{clean_name}** already exists.", ephemeral=True
                )
                return

            item = Item(
                name=clean_name,
                description=(description.strip() if description else None) or None,
                gear_slots=gear_slots if gear_slots is not None else 0.0,
                bundle_size=bundle_size if bundle_size is not None else 1,
                value_cp=value_cp,
                is_magical=magical,
                created_by=str(interaction.user.id),
            )
            session.add(item)
            session.flush()
            embed = build_item_embed(item)
            embed.set_footer(text=f"Added by {interaction.user.display_name}")
            await interaction.response.send_message(embed=embed)

    @items.command(name="info", description="Show details for a catalog item")
    @app_commands.describe(name="Item name to look up")
    async def info(self, interaction: discord.Interaction, name: str) -> None:
        clean_name = name.strip()
        with session_scope() as session:
            item = session.scalar(select(Item).where(Item.name == clean_name))
            if item is None:
                await interaction.response.send_message(
                    f"No catalog entry named **{clean_name}**.", ephemeral=True
                )
                return
            embed = build_item_embed(item)
            await interaction.response.send_message(embed=embed)

    @items.command(name="edit", description="Update fields on an existing catalog item")
    @app_commands.describe(
        name="Item name to edit",
        new_name="Rename the item (must be unique)",
        description="Replace description",
        gear_slots="Replace gear slots",
        bundle_size="Replace bundle size (how many fit in one gear slot)",
        gp="Set value's gold pieces (any of gp/sp/cp replaces the whole value)",
        sp="Set value's silver pieces",
        cp="Set value's copper pieces",
        magical="Change magical/common status",
    )
    async def edit(
        self,
        interaction: discord.Interaction,
        name: str,
        new_name: str | None = None,
        description: str | None = None,
        gear_slots: float | None = None,
        bundle_size: int | None = None,
        gp: int | None = None,
        sp: int | None = None,
        cp: int | None = None,
        magical: bool | None = None,
    ) -> None:
        clean_name = name.strip()
        with session_scope() as session:
            item = session.scalar(select(Item).where(Item.name == clean_name))
            if item is None:
                await interaction.response.send_message(
                    f"No catalog entry named **{clean_name}**.", ephemeral=True
                )
                return

            # If toggling magical status, refuse if there are incompatible references.
            if magical is not None and magical != item.is_magical:
                if magical:
                    blocking = session.scalar(
                        select(func.count())
                        .select_from(InventoryEntry)
                        .where(InventoryEntry.item_id == item.id)
                    ) or 0
                    if blocking:
                        await interaction.response.send_message(
                            f"Cannot mark **{clean_name}** as magical — it still has "
                            f"{blocking} inventory stack(s). Remove them first.",
                            ephemeral=True,
                        )
                        return
                else:
                    blocking = session.scalar(
                        select(func.count())
                        .select_from(TreasuryEntry)
                        .where(TreasuryEntry.item_id == item.id)
                    ) or 0
                    if blocking:
                        await interaction.response.send_message(
                            f"Cannot mark **{clean_name}** as common — it still has "
                            f"{blocking} treasury instance(s). Remove them first.",
                            ephemeral=True,
                        )
                        return

            if bundle_size is not None and bundle_size < 1:
                await interaction.response.send_message(
                    "Bundle size must be ≥ 1.", ephemeral=True
                )
                return

            changed = False
            if new_name is not None:
                clean_new_name = new_name.strip()
                if not clean_new_name:
                    await interaction.response.send_message(
                        "New name cannot be empty.", ephemeral=True
                    )
                    return
                if clean_new_name != item.name:
                    collision = session.scalar(
                        select(Item).where(Item.name == clean_new_name)
                    )
                    if collision is not None:
                        await interaction.response.send_message(
                            f"An item named **{clean_new_name}** already exists.",
                            ephemeral=True,
                        )
                        return
                    item.name = clean_new_name
                    changed = True
            if description is not None:
                item.description = description.strip() or None
                changed = True
            if gear_slots is not None:
                item.gear_slots = gear_slots
                changed = True
            if bundle_size is not None:
                item.bundle_size = bundle_size
                changed = True
            new_value_cp = parse_to_cp(gp, sp, cp)
            if new_value_cp is not None:
                item.value_cp = new_value_cp
                changed = True
            if magical is not None and item.is_magical != magical:
                item.is_magical = magical
                changed = True

            if not changed:
                await interaction.response.send_message(
                    "No changes specified.", ephemeral=True
                )
                return

            session.flush()
            embed = build_item_embed(item)
            embed.set_footer(text=f"Edited by {interaction.user.display_name}")
            await interaction.response.send_message(embed=embed)

    @items.command(name="remove", description="Remove an item from the catalog")
    @app_commands.describe(name="Item name to remove")
    async def remove(self, interaction: discord.Interaction, name: str) -> None:
        clean_name = name.strip()
        with session_scope() as session:
            item = session.scalar(select(Item).where(Item.name == clean_name))
            if item is None:
                await interaction.response.send_message(
                    f"No catalog entry named **{clean_name}**.", ephemeral=True
                )
                return

            inv_count = session.scalar(
                select(func.count())
                .select_from(InventoryEntry)
                .where(InventoryEntry.item_id == item.id)
            ) or 0
            treasury_count = session.scalar(
                select(func.count())
                .select_from(TreasuryEntry)
                .where(TreasuryEntry.item_id == item.id)
            ) or 0
            if inv_count or treasury_count:
                pieces = []
                if inv_count:
                    pieces.append(f"{inv_count} inventory stack(s)")
                if treasury_count:
                    pieces.append(f"{treasury_count} treasury instance(s)")
                await interaction.response.send_message(
                    f"Cannot remove **{clean_name}** — still referenced by "
                    + " and ".join(pieces)
                    + ". Remove those first.",
                    ephemeral=True,
                )
                return

            session.delete(item)
            await interaction.response.send_message(
                f"Removed **{clean_name}** from the catalog.", ephemeral=True
            )

    @items.command(name="list", description="List items in the catalog")
    @app_commands.describe(
        magical="Filter: true=magical only, false=common only, omit=both",
    )
    async def list_items(
        self,
        interaction: discord.Interaction,
        magical: bool | None = None,
    ) -> None:
        with session_scope() as session:
            stmt = select(Item).order_by(Item.name)
            if magical is not None:
                stmt = stmt.where(Item.is_magical.is_(magical))
            all_items = list(session.scalars(stmt).all())

            if not all_items:
                msg = (
                    "No items found with that filter."
                    if magical is not None
                    else "The catalog is empty. Add one with `/items add`."
                )
                await interaction.response.send_message(msg, ephemeral=True)
                return

            common = [it for it in all_items if not it.is_magical]
            magical_items = [it for it in all_items if it.is_magical]

            embed = discord.Embed(
                title=f"Item Catalog ({len(all_items)})",
                color=discord.Color.dark_gray(),
            )
            if common:
                embed.add_field(
                    name=f"Common ({len(common)})",
                    value=_format_list_block(common),
                    inline=False,
                )
            if magical_items:
                embed.add_field(
                    name=f"Magical ({len(magical_items)})",
                    value=_format_list_block(magical_items),
                    inline=False,
                )
            await interaction.response.send_message(embed=embed)

    async def _item_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[app_commands.Choice[str]]:
        with session_scope() as session:
            stmt = (
                select(Item.name)
                .where(Item.name.ilike(f"%{current}%"))
                .order_by(Item.name)
                .limit(25)
            )
            names = list(session.scalars(stmt).all())
        return [app_commands.Choice(name=n, value=n) for n in names]

    @info.autocomplete("name")
    async def _info_autocomplete(self, interaction, current):
        return await self._item_name_autocomplete(interaction, current)

    @edit.autocomplete("name")
    async def _edit_autocomplete(self, interaction, current):
        return await self._item_name_autocomplete(interaction, current)

    @remove.autocomplete("name")
    async def _remove_autocomplete(self, interaction, current):
        return await self._item_name_autocomplete(interaction, current)


def _format_list_block(items: list[Item]) -> str:
    visible = items[:MAX_ITEMS_PER_GROUP]
    lines = [_format_list_line(it) for it in visible]
    if len(items) > MAX_ITEMS_PER_GROUP:
        lines.append(f"…and {len(items) - MAX_ITEMS_PER_GROUP} more")
    return "\n".join(lines)


def _format_list_line(item: Item) -> str:
    return f"• {item.name}"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ItemsDatabase(bot))
