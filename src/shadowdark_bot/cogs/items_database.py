import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from shadowdark_bot.currency import parse_to_cp
from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import build_item_embed
from shadowdark_bot.sharing import ShareableView
from shadowdark_bot.models import (
    ITEM_TYPE_ARMOR,
    ITEM_TYPE_COMMON,
    ITEM_TYPE_CRAFTED,
    ITEM_TYPE_LOOT,
    ITEM_TYPE_MAGICAL,
    ITEM_TYPE_POTION,
    ITEM_TYPE_SCROLL,
    ITEM_TYPE_WEAPON,
    ITEM_TYPES,
    InventoryEntry,
    Item,
    TreasuryEntry,
)

log = logging.getLogger("shadowdark_bot.items")

MAX_ITEMS_PER_GROUP = 25

TYPE_CHOICES = [
    app_commands.Choice(name="common", value=ITEM_TYPE_COMMON),
    app_commands.Choice(name="weapon", value=ITEM_TYPE_WEAPON),
    app_commands.Choice(name="armor", value=ITEM_TYPE_ARMOR),
    app_commands.Choice(name="scroll", value=ITEM_TYPE_SCROLL),
    app_commands.Choice(name="potion", value=ITEM_TYPE_POTION),
    app_commands.Choice(name="loot", value=ITEM_TYPE_LOOT),
    app_commands.Choice(name="crafted", value=ITEM_TYPE_CRAFTED),
    app_commands.Choice(name="magical", value=ITEM_TYPE_MAGICAL),
]


class ItemsDatabase(commands.Cog):
    """Commands for managing the item catalog."""

    items = app_commands.Group(name="items", description="Manage the guild item catalog")

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @items.command(name="add", description="Add a new item to the catalog")
    @app_commands.describe(
        name="Item name (must be unique)",
        type="Item type. Magical items live in the treasury; all others go in inventory.",
        description="Optional description",
        gear_slots="Number of gear slots (Shadow Dark)",
        bundle_size="How many fit in one gear slot (e.g., 20 for arrows). Defaults to 1.",
        gp="Value in gold pieces (1 gp = 10 sp = 100 cp)",
        sp="Value in silver pieces",
        cp="Value in copper pieces",
    )
    @app_commands.choices(type=TYPE_CHOICES)
    async def add(
        self,
        interaction: discord.Interaction,
        name: str,
        type: app_commands.Choice[str],
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
                item_type=type.value,
                created_by=str(interaction.user.id),
            )
            session.add(item)
            session.flush()
            embed = build_item_embed(item)
            embed.set_footer(text=f"Added by {interaction.user.display_name}")
            await interaction.response.send_message(
                embed=embed, view=ShareableView(), ephemeral=True
            )

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
            await interaction.response.send_message(
                embed=embed, view=ShareableView(), ephemeral=True
            )

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
        type="Change item type",
    )
    @app_commands.choices(type=TYPE_CHOICES)
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
        type: app_commands.Choice[str] | None = None,
    ) -> None:
        clean_name = name.strip()
        with session_scope() as session:
            item = session.scalar(select(Item).where(Item.name == clean_name))
            if item is None:
                await interaction.response.send_message(
                    f"No catalog entry named **{clean_name}**.", ephemeral=True
                )
                return

            new_type = type.value if type is not None else None
            # If switching between magical and non-magical, refuse if there
            # are incompatible references.
            if new_type is not None and new_type != item.item_type:
                going_magical = new_type == ITEM_TYPE_MAGICAL
                leaving_magical = item.item_type == ITEM_TYPE_MAGICAL
                if going_magical:
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
                elif leaving_magical:
                    blocking = session.scalar(
                        select(func.count())
                        .select_from(TreasuryEntry)
                        .where(TreasuryEntry.item_id == item.id)
                    ) or 0
                    if blocking:
                        await interaction.response.send_message(
                            f"Cannot change **{clean_name}** from magical — it still "
                            f"has {blocking} treasury instance(s). Remove them first.",
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
            if new_type is not None and item.item_type != new_type:
                item.item_type = new_type
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
    @app_commands.describe(type="Filter to one type (omit to show all)")
    @app_commands.choices(type=TYPE_CHOICES)
    async def list_items(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str] | None = None,
    ) -> None:
        type_val = type.value if type else None
        with session_scope() as session:
            stmt = select(Item).order_by(Item.name)
            if type_val is not None:
                stmt = stmt.where(Item.item_type == type_val)
            all_items = list(session.scalars(stmt).all())

            if not all_items:
                msg = (
                    "No items found with that filter."
                    if type_val is not None
                    else "The catalog is empty. Add one with `/items add`."
                )
                await interaction.response.send_message(msg, ephemeral=True)
                return

            embed = discord.Embed(
                title=f"Item Catalog ({len(all_items)})",
                color=discord.Color.dark_gray(),
            )
            for type_key, label in (
                (ITEM_TYPE_COMMON, "Common"),
                (ITEM_TYPE_WEAPON, "Weapons"),
                (ITEM_TYPE_ARMOR, "Armor"),
                (ITEM_TYPE_SCROLL, "Scrolls"),
                (ITEM_TYPE_POTION, "Potions"),
                (ITEM_TYPE_LOOT, "Loot"),
                (ITEM_TYPE_CRAFTED, "Crafted"),
                (ITEM_TYPE_MAGICAL, "Magical"),
            ):
                group = [it for it in all_items if it.item_type == type_key]
                if group:
                    embed.add_field(
                        name=f"{label} ({len(group)})",
                        value=_format_list_block(group),
                        inline=False,
                    )
            await interaction.response.send_message(
                embed=embed, view=ShareableView(), ephemeral=True
            )

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
