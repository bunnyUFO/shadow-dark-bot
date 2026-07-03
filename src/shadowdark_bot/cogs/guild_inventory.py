import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import (
    build_item_embed,
    build_location_detail_embed,
    build_location_summary_embed,
    fmt_slots,
)
from shadowdark_bot.models import ITEM_TYPE_MAGICAL, InventoryEntry, Item, Location
from shadowdark_bot.rules import stack_slots
from shadowdark_bot.sharing import ShareableView, ShareButton

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
                f"(capacity: {fmt_slots(location.max_gear_slots)} slots).",
                view=ShareableView(),
                ephemeral=True,
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
                f"(capacity: {fmt_slots(location.max_gear_slots)} slots).",
                view=ShareableView(),
                ephemeral=True,
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
                f"Deleted inventory location **{clean_name}**.",
                view=ShareableView(),
                ephemeral=True,
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
        await _do_add(
            interaction,
            location_name=location,
            item_name=item,
            quantity=quantity,
            notes=notes,
        )

    # ---------- Browse ----------

    @inventory.command(
        name="browse",
        description=(
            "Browse inventory: list, inspect, add, and take items. "
            "Optionally jump to one location."
        ),
    )
    @app_commands.describe(
        location="If given, jump straight to one location; otherwise list all locations",
    )
    async def browse(
        self,
        interaction: discord.Interaction,
        location: str | None = None,
    ) -> None:
        if location is None:
            payload = _build_summary_payload()
            if payload is None:
                await interaction.response.send_message(
                    "No inventory locations yet. Create one with "
                    "`/inventory location-create`.",
                    ephemeral=True,
                )
                return
            embed, view = payload
            await interaction.response.send_message(
                embed=embed, view=view, ephemeral=True
            )
            return

        clean_location = location.strip()
        payload = _build_detail_payload(clean_location)
        if payload is None:
            await interaction.response.send_message(
                f"No inventory location named **{clean_location}**.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )

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

    @browse.autocomplete("location")
    async def _ac_browse_location(self, interaction, current):
        return await self._location_autocomplete(interaction, current)


VIEW_TIMEOUT_SECONDS = 300


class _SafeView(discord.ui.View):
    """A view whose component errors surface as an ephemeral message instead of
    a silent "This interaction failed"."""

    async def on_error(
        self,
        interaction: discord.Interaction,
        error: Exception,
        item: discord.ui.Item,
    ) -> None:
        log.exception("Inventory interaction failed on %r", item, exc_info=error)
        message = f"Something went wrong handling that: {error}"[:1900]
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass


def _build_summary_payload() -> tuple[discord.Embed, "InventoryListView"] | None:
    """Build the (embed, view) pair for the all-locations summary.
    Returns None if there are no inventory locations."""
    with session_scope() as session:
        locations = list(
            session.scalars(
                select(Location)
                .where(Location.kind == "inventory")
                .order_by(Location.name)
            ).all()
        )
        if not locations:
            return None

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
        names = [loc.name for loc, _, _ in summaries]
    return embed, InventoryListView(names)


def _build_detail_payload(
    location_name: str,
) -> tuple[discord.Embed, "LocationDetailView"] | None:
    """Build the (embed, view) pair for one location's detail view.
    Returns None if the location doesn't exist."""
    with session_scope() as session:
        loc = session.scalar(
            select(Location).where(
                Location.name == location_name,
                Location.kind == "inventory",
            )
        )
        if loc is None:
            return None
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
        item_names = [stack.item.name for stack in stacks]
    return embed, LocationDetailView(location_name, item_names)


def _build_item_detail_payload(
    location_name: str, item_name: str
) -> tuple[discord.Embed, "ItemDetailView"] | None:
    """Build the (embed, view) pair for an item viewed in the context of a
    location. The embed footer notes how many of the item are at the location.
    Returns None if either the location or item is gone."""
    with session_scope() as session:
        loc = session.scalar(
            select(Location).where(
                Location.name == location_name,
                Location.kind == "inventory",
            )
        )
        if loc is None:
            return None
        item = session.scalar(select(Item).where(Item.name == item_name))
        if item is None:
            return None
        stack = session.scalar(
            select(InventoryEntry).where(
                InventoryEntry.location_id == loc.id,
                InventoryEntry.item_id == item.id,
            )
        )
        stack_qty = stack.quantity if stack else 0
        embed = build_item_embed(item)
        if stack is not None:
            embed.set_footer(text=f"{stack.quantity}× at {location_name}")
    return embed, ItemDetailView(location_name, item_name, stack_qty)


class LocationSelect(discord.ui.Select):
    """Dropdown listing all locations. Picking one swaps the embed to detail."""

    def __init__(self, location_names: list[str]) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=name[:100])
            for name in location_names[:25]
        ]
        super().__init__(
            placeholder="Expand a location…",
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        location_name = self.values[0]
        payload = _build_detail_payload(location_name)
        if payload is None:
            await interaction.response.send_message(
                f"**{location_name}** no longer exists.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class InventoryListView(_SafeView):
    """Summary embed with a dropdown to drill into a location plus a share button."""

    def __init__(self, location_names: list[str]) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.add_item(LocationSelect(location_names))
        self.add_item(ShareButton(row=4))


class LocationItemSelect(discord.ui.Select):
    """Dropdown listing items present at a location. Pick one to inspect it."""

    def __init__(self, location_name: str, item_names: list[str]) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=name[:100])
            for name in item_names[:25]
        ]
        super().__init__(
            placeholder="Inspect an item…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.location_name = location_name

    async def callback(self, interaction: discord.Interaction) -> None:
        item_name = self.values[0]
        payload = _build_item_detail_payload(self.location_name, item_name)
        if payload is None:
            await interaction.response.send_message(
                f"**{item_name}** is no longer here.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class LocationDetailView(_SafeView):
    """Detail embed: item-picker dropdown (if stocked) + back + share. Adding
    new items goes through `/inventory add`."""

    def __init__(self, location_name: str, item_names: list[str]) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.location_name = location_name
        if item_names:
            self.add_item(LocationItemSelect(location_name, item_names))
        self.add_item(ShareButton(row=4))

    @discord.ui.button(
        label="← Back to all locations",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_summary_payload()
        if payload is None:
            await interaction.response.edit_message(
                content="No inventory locations remain.",
                embed=None,
                view=None,
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class ItemDetailView(_SafeView):
    """Item-info embed shown in the context of a location. Has Add-more / Take
    quick-action buttons (Take is disabled for empty stacks) plus Back to the
    location detail view. Adding an item that isn't already at the location
    goes through `/inventory add`."""

    def __init__(
        self,
        location_name: str,
        item_name: str,
        stack_qty: int,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.location_name = location_name
        self.item_name = item_name
        self.add_item(AddMoreButton(location_name, item_name, row=0))
        self.add_item(
            TakeButton(location_name, item_name, stack_qty, row=0)
        )

        # Built manually so the label can include the location name.
        back_btn = discord.ui.Button(
            label=f"← Back to {location_name[:60]}",
            style=discord.ButtonStyle.primary,
            row=1,
        )
        back_btn.callback = self._back  # type: ignore[method-assign]
        self.add_item(back_btn)
        self.add_item(ShareButton(row=4))

    async def _back(self, interaction: discord.Interaction) -> None:
        payload = _build_detail_payload(self.location_name)
        if payload is None:
            await interaction.response.send_message(
                f"**{self.location_name}** no longer exists.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


def _used_slots(session: Session, location_id: int) -> float:
    """Sum of stack_slots() across all stacks at a location.
    Computed in Python so bundle ceiling rule applies per-stack."""
    rows = session.execute(
        select(InventoryEntry.quantity, Item.gear_slots, Item.bundle_size)
        .join(Item, Item.id == InventoryEntry.item_id)
        .where(InventoryEntry.location_id == location_id)
    ).all()
    return float(sum(stack_slots(q, g, b) for q, g, b in rows))


# ---------- Add-more / Take modals + buttons (item detail view only) ----------


class AddMoreModal(discord.ui.Modal):
    """Opened from the item detail view '+ Add more'. Item is pre-known;
    only the quantity (and optional notes) is needed."""

    def __init__(self, location_name: str, item_name: str) -> None:
        super().__init__(title=f"Add more {item_name}"[:45])
        self.location_name = location_name
        self.item_name = item_name

        self._qty = discord.ui.TextInput(
            label="Quantity",
            required=True,
            default="1",
            max_length=8,
        )
        self._notes = discord.ui.TextInput(
            label="Notes (optional)",
            required=False,
            max_length=200,
        )
        self.add_item(self._qty)
        self.add_item(self._notes)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            quantity = int(str(self._qty.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "**Failed to add.**\nQuantity must be a whole number.",
                ephemeral=True,
            )
            return
        await _do_add(
            interaction,
            location_name=self.location_name,
            item_name=self.item_name,
            quantity=quantity,
            notes=str(self._notes.value) if self._notes.value else None,
            edit_view=True,
        )


class TakeModal(discord.ui.Modal):
    """Opened from the item detail view ('- Take'). Item is pre-known; only the
    quantity is needed."""

    def __init__(self, location_name: str, item_name: str, stack_qty: int) -> None:
        super().__init__(title=f"Take from {location_name}"[:45])
        self.location_name = location_name
        self.item_name = item_name

        self._qty = discord.ui.TextInput(
            label=f"Quantity (1–{stack_qty})",
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
                "**Failed to take.**\nQuantity must be a whole number.",
                ephemeral=True,
            )
            return
        await _do_take(
            interaction,
            location_name=self.location_name,
            item_name=self.item_name,
            quantity=quantity,
            edit_view=True,
        )


class AddMoreButton(discord.ui.Button):
    def __init__(self, location_name: str, item_name: str, row: int = 0) -> None:
        super().__init__(
            label="+ Add more",
            style=discord.ButtonStyle.success,
            row=row,
        )
        self.location_name = location_name
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            AddMoreModal(self.location_name, self.item_name)
        )


class TakeButton(discord.ui.Button):
    def __init__(
        self,
        location_name: str,
        item_name: str,
        stack_qty: int,
        row: int = 0,
    ) -> None:
        super().__init__(
            label="− Take",
            style=discord.ButtonStyle.danger,
            row=row,
            disabled=stack_qty <= 0,
        )
        self.location_name = location_name
        self.item_name = item_name
        self.stack_qty = stack_qty

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            TakeModal(self.location_name, self.item_name, self.stack_qty)
        )


# ---------- Shared add/take logic ----------


async def _do_add(
    interaction: discord.Interaction,
    *,
    location_name: str,
    item_name: str,
    quantity: int,
    notes: str | None = None,
    edit_view: bool = False,
) -> None:
    clean_location = location_name.strip()
    clean_item = item_name.strip()
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
                f"{failure}\n**{clean_item}** is magical — magical items go in "
                "the treasury, not inventory.",
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
        cap = loc.max_gear_slots
        if new_used > cap:
            await interaction.response.send_message(
                f"{failure}\n**{clean_location}** has "
                f"{fmt_slots(used)}/{fmt_slots(cap)} slots used; "
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
        final_stack_qty = stack.quantity

    confirmation = (
        f"Added {quantity}× **{clean_item}** to **{clean_location}**. "
        f"Stack: {final_stack_qty}. "
        f"Capacity: {fmt_slots(new_used)}/{fmt_slots(cap)} slots."
    )
    await _respond(
        interaction, confirmation, clean_location, clean_item, edit_view
    )


async def _do_take(
    interaction: discord.Interaction,
    *,
    location_name: str,
    item_name: str,
    quantity: int,
    edit_view: bool = False,
) -> None:
    clean_location = location_name.strip()
    clean_item = item_name.strip()
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
        emptied = stack.quantity == 0
        if emptied:
            session.delete(stack)

    if emptied:
        confirmation = (
            f"Took the last {quantity}× **{clean_item}** from **{clean_location}**. "
            "Stack removed."
        )
    else:
        confirmation = (
            f"Took {quantity}× **{clean_item}** from **{clean_location}**. "
            f"{stack.quantity} remaining."
        )
    await _respond(
        interaction, confirmation, clean_location, clean_item, edit_view
    )


async def _respond(
    interaction: discord.Interaction,
    confirmation: str,
    location_name: str,
    item_name: str,
    edit_view: bool,
) -> None:
    """Send the confirmation. For modal-driven actions also refresh the parent
    item detail view so the embed footer reflects the new stack count (or the
    stack disappears if the take emptied it)."""
    if edit_view:
        payload = _build_item_detail_payload(location_name, item_name)
        if payload is not None:
            embed, view = payload
            await interaction.response.edit_message(embed=embed, view=view)
        else:
            await interaction.response.defer()
        await interaction.followup.send(
            confirmation, view=ShareableView(), ephemeral=True
        )
    else:
        await interaction.response.send_message(
            confirmation, view=ShareableView(), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GuildInventory(bot))
