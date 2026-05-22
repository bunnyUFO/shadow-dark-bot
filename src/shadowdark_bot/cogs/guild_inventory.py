import logging
import math

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import (
    build_location_detail_embed,
    build_location_summary_embed,
    fmt_slots,
)
from shadowdark_bot.models import ITEM_TYPE_MAGICAL, InventoryEntry, Item, Location


def stack_slots(quantity: int, gear_slots: float, bundle_size: int) -> float:
    """Slot cost for `quantity` of an item: ceil(quantity / bundle_size) * gear_slots.
    A partial bundle still consumes a full bundle's worth of slots."""
    return math.ceil(quantity / max(bundle_size, 1)) * gear_slots

log = logging.getLogger("shadowdark_bot.inventory")


class GuildInventory(commands.Cog):
    """Commands for managing shared non-magical inventory."""

    inventory = app_commands.Group(
        name="inventory", description="Manage shared guild inventory"
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---------- Location commands ----------

    @inventory.command(
        name="location-create",
        description="Create a new shared inventory location",
    )
    @app_commands.describe(
        name="Location name (must be unique)",
        max_gear_slots="Max capacity in gear slots (defaults to 0)",
        description="Optional description",
    )
    async def location_create(
        self,
        interaction: discord.Interaction,
        name: str,
        max_gear_slots: float | None = None,
        description: str | None = None,
    ) -> None:
        clean_name = name.strip()
        if not clean_name:
            await interaction.response.send_message("Name cannot be empty.", ephemeral=True)
            return
        if max_gear_slots is not None and max_gear_slots < 0:
            await interaction.response.send_message("Capacity must be ≥ 0.", ephemeral=True)
            return

        cap = max_gear_slots if max_gear_slots is not None else 0.0

        with session_scope() as session:
            existing = session.scalar(select(Location).where(Location.name == clean_name))
            if existing is not None:
                await interaction.response.send_message(
                    f"A location named **{clean_name}** already exists.", ephemeral=True
                )
                return
            location = Location(
                name=clean_name,
                kind="inventory",
                max_gear_slots=cap,
                description=(description.strip() if description else None) or None,
            )
            session.add(location)
            session.flush()
            await interaction.response.send_message(
                f"Created inventory location **{clean_name}** "
                f"(capacity: {fmt_slots(location.max_gear_slots)} slots)."
            )

    @inventory.command(
        name="location-edit",
        description="Edit an inventory location's capacity or description",
    )
    @app_commands.describe(
        name="Location to edit",
        max_gear_slots="New capacity (must be ≥ currently-used slots)",
        description="Replace description",
    )
    async def location_edit(
        self,
        interaction: discord.Interaction,
        name: str,
        max_gear_slots: float | None = None,
        description: str | None = None,
    ) -> None:
        clean_name = name.strip()
        with session_scope() as session:
            location = session.scalar(
                select(Location).where(
                    Location.name == clean_name, Location.kind == "inventory"
                )
            )
            if location is None:
                await interaction.response.send_message(
                    f"No inventory location named **{clean_name}**.", ephemeral=True
                )
                return

            changed = False

            if max_gear_slots is not None:
                if max_gear_slots < 0:
                    await interaction.response.send_message(
                        "Capacity must be ≥ 0.", ephemeral=True
                    )
                    return
                used = _used_slots(session, location.id)
                if max_gear_slots < used:
                    await interaction.response.send_message(
                        f"**{clean_name}** has {fmt_slots(used)} slots in use; "
                        f"can't shrink capacity to {fmt_slots(max_gear_slots)}. "
                        "Take items out first.",
                        ephemeral=True,
                    )
                    return
                location.max_gear_slots = max_gear_slots
                changed = True

            if description is not None:
                location.description = description.strip() or None
                changed = True

            if not changed:
                await interaction.response.send_message(
                    "No changes specified.", ephemeral=True
                )
                return

            session.flush()
            await interaction.response.send_message(
                f"Updated **{clean_name}** "
                f"(capacity: {fmt_slots(location.max_gear_slots)} slots)."
            )

    @inventory.command(
        name="location-delete",
        description="Delete an inventory location (must be empty)",
    )
    @app_commands.describe(name="Location to delete")
    async def location_delete(self, interaction: discord.Interaction, name: str) -> None:
        clean_name = name.strip()
        with session_scope() as session:
            location = session.scalar(
                select(Location).where(
                    Location.name == clean_name, Location.kind == "inventory"
                )
            )
            if location is None:
                await interaction.response.send_message(
                    f"No inventory location named **{clean_name}**.", ephemeral=True
                )
                return
            count = (
                session.scalar(
                    select(func.count())
                    .select_from(InventoryEntry)
                    .where(InventoryEntry.location_id == location.id)
                )
                or 0
            )
            if count:
                await interaction.response.send_message(
                    f"**{clean_name}** still has {count} stack(s). Empty it first.",
                    ephemeral=True,
                )
                return
            session.delete(location)
            await interaction.response.send_message(
                f"Deleted inventory location **{clean_name}**.", ephemeral=True
            )

    # ---------- Add / take ----------

    @inventory.command(name="add", description="Add items to an inventory location")
    @app_commands.describe(
        location="Where to add the items",
        item="Item to add (must be non-magical)",
        quantity="Number to add (defaults to 1)",
        notes="Optional note for this stack",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        location: str,
        item: str,
        quantity: int = 1,
        notes: str | None = None,
    ) -> None:
        clean_location = location.strip()
        clean_item = item.strip()
        failure = f"**Failed to add {quantity}× {clean_item} to {clean_location}.**"

        if quantity < 1:
            await interaction.response.send_message(
                f"{failure}\nQuantity must be ≥ 1.", ephemeral=True
            )
            return

        with session_scope() as session:
            loc = session.scalar(
                select(Location).where(
                    Location.name == clean_location, Location.kind == "inventory"
                )
            )
            if loc is None:
                await interaction.response.send_message(
                    f"{failure}\nNo inventory location named **{clean_location}**.",
                    ephemeral=True,
                )
                return

            cat_item = session.scalar(select(Item).where(Item.name == clean_item))
            if cat_item is None:
                await interaction.response.send_message(
                    f"{failure}\nNo catalog entry named **{clean_item}**.",
                    ephemeral=True,
                )
                return
            if cat_item.item_type == ITEM_TYPE_MAGICAL:
                await interaction.response.send_message(
                    f"{failure}\n**{clean_item}** is magical — magical items go in the "
                    "treasury, not inventory.",
                    ephemeral=True,
                )
                return

            stack = session.scalar(
                select(InventoryEntry).where(
                    InventoryEntry.location_id == loc.id,
                    InventoryEntry.item_id == cat_item.id,
                )
            )
            current_qty = stack.quantity if stack else 0
            new_qty = current_qty + quantity
            current_stack_slots = stack_slots(
                current_qty, cat_item.gear_slots, cat_item.bundle_size
            )
            new_stack_slots = stack_slots(
                new_qty, cat_item.gear_slots, cat_item.bundle_size
            )
            proposed_delta = new_stack_slots - current_stack_slots

            used = _used_slots(session, loc.id)
            new_used = used + proposed_delta
            if new_used > loc.max_gear_slots:
                await interaction.response.send_message(
                    f"{failure}\n**{clean_location}** has "
                    f"{fmt_slots(used)}/{fmt_slots(loc.max_gear_slots)} slots used; "
                    f"adding {quantity}× **{clean_item}** "
                    f"({fmt_slots(proposed_delta)} more slots) "
                    f"would total {fmt_slots(new_used)}. "
                    "Raise the capacity with `/inventory location-edit` or add less.",
                    ephemeral=True,
                )
                return

            if stack is None:
                stack = InventoryEntry(
                    location_id=loc.id,
                    item_id=cat_item.id,
                    quantity=quantity,
                    notes=(notes.strip() if notes else None) or None,
                    added_by=str(interaction.user.id),
                )
                session.add(stack)
            else:
                stack.quantity = new_qty
                if notes:
                    stack.notes = notes.strip() or None
            session.flush()

            await interaction.response.send_message(
                f"Added {quantity}× **{clean_item}** to **{clean_location}**. "
                f"Stack: {stack.quantity}. "
                f"Capacity: {fmt_slots(new_used)}/{fmt_slots(loc.max_gear_slots)} slots."
            )

    @inventory.command(name="take", description="Take items from an inventory location")
    @app_commands.describe(
        location="Where to take from",
        item="Item to take",
        quantity="Number to take (defaults to 1)",
    )
    async def take(
        self,
        interaction: discord.Interaction,
        location: str,
        item: str,
        quantity: int = 1,
    ) -> None:
        clean_location = location.strip()
        clean_item = item.strip()
        failure = f"**Failed to take {quantity}× {clean_item} from {clean_location}.**"

        if quantity < 1:
            await interaction.response.send_message(
                f"{failure}\nQuantity must be ≥ 1.", ephemeral=True
            )
            return

        with session_scope() as session:
            loc = session.scalar(
                select(Location).where(
                    Location.name == clean_location, Location.kind == "inventory"
                )
            )
            if loc is None:
                await interaction.response.send_message(
                    f"{failure}\nNo inventory location named **{clean_location}**.",
                    ephemeral=True,
                )
                return
            cat_item = session.scalar(select(Item).where(Item.name == clean_item))
            if cat_item is None:
                await interaction.response.send_message(
                    f"{failure}\nNo catalog entry named **{clean_item}**.",
                    ephemeral=True,
                )
                return
            stack = session.scalar(
                select(InventoryEntry).where(
                    InventoryEntry.location_id == loc.id,
                    InventoryEntry.item_id == cat_item.id,
                )
            )
            if stack is None:
                await interaction.response.send_message(
                    f"{failure}\nNo **{clean_item}** at **{clean_location}**.",
                    ephemeral=True,
                )
                return
            if quantity > stack.quantity:
                await interaction.response.send_message(
                    f"{failure}\nOnly {stack.quantity} **{clean_item}** at "
                    f"**{clean_location}**; can't take {quantity}.",
                    ephemeral=True,
                )
                return

            stack.quantity -= quantity
            if stack.quantity == 0:
                session.delete(stack)
                await interaction.response.send_message(
                    f"Took the last {quantity}× **{clean_item}** from **{clean_location}**. "
                    "Stack removed."
                )
            else:
                await interaction.response.send_message(
                    f"Took {quantity}× **{clean_item}** from **{clean_location}**. "
                    f"{stack.quantity} remaining."
                )

    # ---------- List ----------

    @inventory.command(name="list", description="Show inventory contents")
    @app_commands.describe(
        location="If given, show details for one location; otherwise list all locations",
    )
    async def list_inventory(
        self,
        interaction: discord.Interaction,
        location: str | None = None,
    ) -> None:
        with session_scope() as session:
            if location is None:
                locations = list(
                    session.scalars(
                        select(Location)
                        .where(Location.kind == "inventory")
                        .order_by(Location.name)
                    ).all()
                )
                if not locations:
                    await interaction.response.send_message(
                        "No inventory locations yet. Create one with "
                        "`/inventory location-create`.",
                        ephemeral=True,
                    )
                    return

                summaries: list[tuple[Location, float, int]] = []
                for loc in locations:
                    used = _used_slots(session, loc.id)
                    count = (
                        session.scalar(
                            select(func.count())
                            .select_from(InventoryEntry)
                            .where(InventoryEntry.location_id == loc.id)
                        )
                        or 0
                    )
                    summaries.append((loc, used, count))

                embed = build_location_summary_embed(summaries)
                await interaction.response.send_message(embed=embed)
                return

            clean_location = location.strip()
            loc = session.scalar(
                select(Location).where(
                    Location.name == clean_location, Location.kind == "inventory"
                )
            )
            if loc is None:
                await interaction.response.send_message(
                    f"No inventory location named **{clean_location}**.", ephemeral=True
                )
                return
            stacks = list(
                session.scalars(
                    select(InventoryEntry)
                    .options(joinedload(InventoryEntry.item))
                    .where(InventoryEntry.location_id == loc.id)
                    .order_by(InventoryEntry.id)
                ).all()
            )
            used = _used_slots(session, loc.id)
            embed = build_location_detail_embed(loc, stacks, used)
            await interaction.response.send_message(embed=embed)

    # ---------- Autocompletes ----------

    async def _location_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        with session_scope() as session:
            stmt = (
                select(Location.name)
                .where(
                    Location.kind == "inventory",
                    Location.name.ilike(f"%{current}%"),
                )
                .order_by(Location.name)
                .limit(25)
            )
            names = list(session.scalars(stmt).all())
        return [app_commands.Choice(name=n, value=n) for n in names]

    async def _nonmagical_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        with session_scope() as session:
            stmt = (
                select(Item.name)
                .where(
                    Item.item_type != ITEM_TYPE_MAGICAL,
                    Item.name.ilike(f"%{current}%"),
                )
                .order_by(Item.name)
                .limit(25)
            )
            names = list(session.scalars(stmt).all())
        return [app_commands.Choice(name=n, value=n) for n in names]

    @location_edit.autocomplete("name")
    async def _ac_location_edit(self, interaction, current):
        return await self._location_autocomplete(interaction, current)

    @location_delete.autocomplete("name")
    async def _ac_location_delete(self, interaction, current):
        return await self._location_autocomplete(interaction, current)

    @add.autocomplete("location")
    async def _ac_add_location(self, interaction, current):
        return await self._location_autocomplete(interaction, current)

    @add.autocomplete("item")
    async def _ac_add_item(self, interaction, current):
        return await self._nonmagical_item_autocomplete(interaction, current)

    @take.autocomplete("location")
    async def _ac_take_location(self, interaction, current):
        return await self._location_autocomplete(interaction, current)

    @take.autocomplete("item")
    async def _ac_take_item(self, interaction, current):
        return await self._nonmagical_item_autocomplete(interaction, current)

    @list_inventory.autocomplete("location")
    async def _ac_list_location(self, interaction, current):
        return await self._location_autocomplete(interaction, current)


def _used_slots(session: Session, location_id: int) -> float:
    """Sum of stack_slots() across all stacks at a location.
    Computed in Python so bundle ceiling rule applies per-stack."""
    rows = session.execute(
        select(InventoryEntry.quantity, Item.gear_slots, Item.bundle_size)
        .join(Item, Item.id == InventoryEntry.item_id)
        .where(InventoryEntry.location_id == location_id)
    ).all()
    return float(sum(stack_slots(q, g, b) for q, g, b in rows))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildInventory(bot))
