import logging

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

from shadowdark_bot.currency import format_cp, parse_to_cp, parse_value_string
from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import build_item_embed
from shadowdark_bot.sharing import ShareButton, ShareableView
from shadowdark_bot.models import (
    ITEM_TYPE_ARMOR,
    ITEM_TYPE_COMMON,
    ITEM_TYPE_CRAFTED,
    ITEM_TYPE_LOOT,
    ITEM_TYPE_MAGICAL,
    ITEM_TYPE_POTION,
    ITEM_TYPE_SCROLL,
    ITEM_TYPE_WEAPON,
    InventoryEntry,
    Item,
    TreasuryEntry,
)

log = logging.getLogger("shadowdark_bot.items")

MAX_ITEMS_PER_GROUP = 25
PAGE_SIZE = 25  # Discord caps a string-select at 25 options
VIEW_TIMEOUT_SECONDS = 300

_TYPE_GROUPS: tuple[tuple[str, str], ...] = (
    (ITEM_TYPE_COMMON, "Common"),
    (ITEM_TYPE_WEAPON, "Weapons"),
    (ITEM_TYPE_ARMOR, "Armor"),
    (ITEM_TYPE_SCROLL, "Scrolls"),
    (ITEM_TYPE_POTION, "Potions"),
    (ITEM_TYPE_LOOT, "Loot"),
    (ITEM_TYPE_CRAFTED, "Crafted"),
    (ITEM_TYPE_MAGICAL, "Magical"),
)
_TYPE_LABEL: dict[str, str] = {k: v for k, v in _TYPE_GROUPS}

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
        gear_slots="Number of gear slots (Shadow Dark). Defaults to 1.",
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
                gear_slots=gear_slots if gear_slots is not None else 1.0,
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

    @items.command(
        name="edit",
        description="Edit a catalog item — opens a form pre-filled with current values",
    )
    @app_commands.describe(
        name="Item name to edit",
        type="Optional: also change the item's type",
    )
    @app_commands.choices(type=TYPE_CHOICES)
    async def edit(
        self,
        interaction: discord.Interaction,
        name: str,
        type: app_commands.Choice[str] | None = None,
    ) -> None:
        clean_name = name.strip()
        new_type = type.value if type is not None else None
        with session_scope() as session:
            item = session.scalar(select(Item).where(Item.name == clean_name))
            if item is None:
                await interaction.response.send_message(
                    f"No catalog entry named **{clean_name}**.", ephemeral=True
                )
                return

            err = _check_type_change(session, item, new_type)
            if err is not None:
                await interaction.response.send_message(err, ephemeral=True)
                return

            modal = EditItemModal.from_item(item, new_type=new_type)
        await interaction.response.send_modal(modal)

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
                f"Removed **{clean_name}** from the catalog.",
                view=ShareableView(),
                ephemeral=True,
            )

    @items.command(
        name="browse",
        description="Browse the catalog: filter by type, inspect an item, open the edit form",
    )
    @app_commands.describe(type="Filter to one type (omit to show all)")
    @app_commands.choices(type=TYPE_CHOICES)
    async def browse(
        self,
        interaction: discord.Interaction,
        type: app_commands.Choice[str] | None = None,
    ) -> None:
        type_val = type.value if type else None
        payload = _build_catalog_list_payload(type_val)
        if payload is None:
            msg = (
                "No items found with that filter."
                if type_val is not None
                else "The catalog is empty. Add one with `/items add`."
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return
        embed, view = payload
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
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


# ---------- Type-change guard (shared by /items edit slash and modal) ----------


def _check_type_change(session, item: Item, new_type: str | None) -> str | None:
    """Return an error string if changing item.item_type to new_type is blocked
    by existing inventory/treasury references, else None."""
    if new_type is None or new_type == item.item_type:
        return None
    going_magical = new_type == ITEM_TYPE_MAGICAL
    leaving_magical = item.item_type == ITEM_TYPE_MAGICAL
    if going_magical:
        blocking = session.scalar(
            select(func.count())
            .select_from(InventoryEntry)
            .where(InventoryEntry.item_id == item.id)
        ) or 0
        if blocking:
            return (
                f"Cannot mark **{item.name}** as magical — it still has "
                f"{blocking} inventory stack(s). Remove them first."
            )
    elif leaving_magical:
        blocking = session.scalar(
            select(func.count())
            .select_from(TreasuryEntry)
            .where(TreasuryEntry.item_id == item.id)
        ) or 0
        if blocking:
            return (
                f"Cannot change **{item.name}** from magical — it still "
                f"has {blocking} treasury instance(s). Remove them first."
            )
    return None


# ---------- Edit modal ----------


class EditItemModal(discord.ui.Modal):
    """5-field edit form. Type changes are passed in via new_type (from the
    slash command's optional `type:` arg); they're applied on submit alongside
    the form fields."""

    def __init__(
        self,
        *,
        item_id: int,
        original_name: str,
        default_name: str,
        default_description: str,
        default_gear_slots: str,
        default_bundle_size: str,
        default_value: str,
        new_type: str | None = None,
    ) -> None:
        super().__init__(title=f"Edit {original_name}"[:45])
        self.item_id = item_id
        self.original_name = original_name
        self.new_type = new_type

        self._name = discord.ui.TextInput(
            label="Name",
            required=True,
            default=default_name,
            max_length=100,
        )
        self._description = discord.ui.TextInput(
            label="Description",
            required=False,
            default=default_description,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self._gear_slots = discord.ui.TextInput(
            label="Gear slots",
            required=True,
            default=default_gear_slots,
            max_length=10,
        )
        self._bundle_size = discord.ui.TextInput(
            label="Bundle size (items per slot)",
            required=True,
            default=default_bundle_size,
            max_length=10,
        )
        self._value = discord.ui.TextInput(
            label='Value (e.g. "5gp 2sp"; blank = unset)',
            required=False,
            default=default_value,
            max_length=50,
        )
        self.add_item(self._name)
        self.add_item(self._description)
        self.add_item(self._gear_slots)
        self.add_item(self._bundle_size)
        self.add_item(self._value)

    @classmethod
    def from_item(cls, item: Item, *, new_type: str | None = None) -> "EditItemModal":
        return cls(
            item_id=item.id,
            original_name=item.name,
            default_name=item.name,
            default_description=item.description or "",
            default_gear_slots=f"{item.gear_slots:g}",
            default_bundle_size=str(item.bundle_size),
            default_value=format_cp(item.value_cp) or "",
            new_type=new_type,
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        new_name = str(self._name.value).strip()
        if not new_name:
            await interaction.response.send_message(
                "**Failed to save.**\nName cannot be empty.", ephemeral=True
            )
            return

        try:
            gear_slots = float(str(self._gear_slots.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "**Failed to save.**\nGear slots must be a number.", ephemeral=True
            )
            return
        if gear_slots < 0:
            await interaction.response.send_message(
                "**Failed to save.**\nGear slots must be ≥ 0.", ephemeral=True
            )
            return

        try:
            bundle_size = int(str(self._bundle_size.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "**Failed to save.**\nBundle size must be a whole number.",
                ephemeral=True,
            )
            return
        if bundle_size < 1:
            await interaction.response.send_message(
                "**Failed to save.**\nBundle size must be ≥ 1.", ephemeral=True
            )
            return

        try:
            value_cp = parse_value_string(str(self._value.value))
        except ValueError as err:
            await interaction.response.send_message(
                f"**Failed to save.**\n{err}", ephemeral=True
            )
            return

        description = str(self._description.value).strip() or None

        with session_scope() as session:
            item = session.get(Item, self.item_id)
            if item is None:
                await interaction.response.send_message(
                    f"**{self.original_name}** no longer exists.", ephemeral=True
                )
                return

            if new_name != item.name:
                collision = session.scalar(
                    select(Item).where(
                        Item.name == new_name, Item.id != item.id
                    )
                )
                if collision is not None:
                    await interaction.response.send_message(
                        f"**Failed to save.**\nAn item named **{new_name}** "
                        "already exists.",
                        ephemeral=True,
                    )
                    return

            err = _check_type_change(session, item, self.new_type)
            if err is not None:
                await interaction.response.send_message(err, ephemeral=True)
                return

            item.name = new_name
            item.description = description
            item.gear_slots = gear_slots
            item.bundle_size = bundle_size
            item.value_cp = value_cp
            if self.new_type is not None:
                item.item_type = self.new_type
            session.flush()

            embed = build_item_embed(item)
            embed.set_footer(text=f"Edited by {interaction.user.display_name}")

        await interaction.response.send_message(
            embed=embed, view=ShareableView(), ephemeral=True
        )


# ---------- Browse views ----------


def _build_catalog_list_payload(
    type_val: str | None,
    page: int = 0,
) -> tuple[discord.Embed, "CatalogListView"] | None:
    """Build (embed, view) for the browse list. None if no items match.

    The dropdown is page-sliced (PAGE_SIZE items at a time) with Prev/Next
    buttons; the embed itself shows the full grouped overview so users see
    everything at a glance."""
    with session_scope() as session:
        stmt = select(Item).order_by(Item.name)
        if type_val is not None:
            stmt = stmt.where(Item.item_type == type_val)
        all_items = list(session.scalars(stmt).all())
        if not all_items:
            return None

        total = len(all_items)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))

        if type_val is not None:
            label = _TYPE_LABEL.get(type_val, "Items")
            title = f"{label} ({total})"
        else:
            title = f"Item Catalog ({total})"
        embed = discord.Embed(title=title, color=discord.Color.dark_gray())

        if type_val is not None:
            visible = all_items[:MAX_ITEMS_PER_GROUP]
            lines = [_format_list_line(it) for it in visible]
            if total > MAX_ITEMS_PER_GROUP:
                lines.append(f"…and {total - MAX_ITEMS_PER_GROUP} more")
            embed.description = "\n".join(lines)
        else:
            for type_key, label in _TYPE_GROUPS:
                group = [it for it in all_items if it.item_type == type_key]
                if group:
                    embed.add_field(
                        name=f"{label} ({len(group)})",
                        value=_format_list_block(group),
                        inline=False,
                    )

        start = page * PAGE_SIZE
        page_items = all_items[start:start + PAGE_SIZE]
        page_names = [it.name for it in page_items]

        if total_pages > 1 and page_items:
            first = page_items[0].name
            last = page_items[-1].name
            range_str = first if first == last else f"{first} → {last}"
            embed.set_footer(
                text=(
                    f"Dropdown page {page + 1} of {total_pages} · "
                    f"{range_str} · use type: to narrow."
                )
            )
    return embed, CatalogListView(page_names, type_val, page, total_pages)


def _build_catalog_item_detail_payload(
    item_name: str,
    type_val: str | None,
    page: int = 0,
) -> tuple[discord.Embed, "CatalogItemDetailView"] | None:
    """Build (embed, view) for one item drilled into from browse. `page` is
    the catalog page the user came from, so Back returns to the same page."""
    with session_scope() as session:
        item = session.scalar(select(Item).where(Item.name == item_name))
        if item is None:
            return None
        embed = build_item_embed(item)
        item_id = item.id
        original_name = item.name
        default_description = item.description or ""
        default_gear_slots = f"{item.gear_slots:g}"
        default_bundle_size = str(item.bundle_size)
        default_value = format_cp(item.value_cp) or ""
    return embed, CatalogItemDetailView(
        type_val=type_val,
        page=page,
        modal_kwargs=dict(
            item_id=item_id,
            original_name=original_name,
            default_name=original_name,
            default_description=default_description,
            default_gear_slots=default_gear_slots,
            default_bundle_size=default_bundle_size,
            default_value=default_value,
        ),
    )


class CatalogItemSelect(discord.ui.Select):
    """Dropdown listing catalog items. Picking one drills into its detail."""

    def __init__(
        self,
        item_names: list[str],
        type_val: str | None,
        page: int,
    ) -> None:
        options = [
            discord.SelectOption(label=name[:100], value=name[:100])
            for name in item_names[:PAGE_SIZE]
        ]
        super().__init__(
            placeholder="Inspect an item…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.type_val = type_val
        self.page = page

    async def callback(self, interaction: discord.Interaction) -> None:
        item_name = self.values[0]
        payload = _build_catalog_item_detail_payload(
            item_name, self.type_val, self.page
        )
        if payload is None:
            await interaction.response.send_message(
                f"**{item_name}** no longer exists.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class _PageButton(discord.ui.Button):
    """Base class for the Prev/Next pagination buttons."""

    def __init__(self, *, label: str, delta: int, disabled: bool) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary,
            row=1,
            disabled=disabled,
        )
        self.delta = delta

    async def callback(self, interaction: discord.Interaction) -> None:
        view: "CatalogListView" = self.view  # type: ignore[assignment]
        payload = _build_catalog_list_payload(
            view.type_val, page=view.page + self.delta
        )
        if payload is None:
            await interaction.response.edit_message(
                content="The catalog is now empty.", embed=None, view=None
            )
            return
        embed, new_view = payload
        await interaction.response.edit_message(embed=embed, view=new_view)


class CatalogListView(discord.ui.View):
    """Browse list embed with item-picker dropdown, optional Prev/Next, share."""

    def __init__(
        self,
        item_names: list[str],
        type_val: str | None,
        page: int,
        total_pages: int,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.type_val = type_val
        self.page = page
        self.total_pages = total_pages
        if item_names:
            self.add_item(CatalogItemSelect(item_names, type_val, page))
        if total_pages > 1:
            self.add_item(
                _PageButton(label="← Prev", delta=-1, disabled=(page == 0))
            )
            self.add_item(
                _PageButton(
                    label="Next →",
                    delta=1,
                    disabled=(page >= total_pages - 1),
                )
            )
        self.add_item(ShareButton(row=4))


class EditItemButton(discord.ui.Button):
    def __init__(self, modal_kwargs: dict) -> None:
        super().__init__(label="Edit", style=discord.ButtonStyle.primary, row=0)
        self._modal_kwargs = modal_kwargs

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(EditItemModal(**self._modal_kwargs))


class CatalogItemDetailView(discord.ui.View):
    """Item-detail embed with Edit + Back + Share. Back returns to the page
    the user came from."""

    def __init__(
        self,
        *,
        type_val: str | None,
        page: int,
        modal_kwargs: dict,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.type_val = type_val
        self.page = page
        self.add_item(EditItemButton(modal_kwargs))
        self.add_item(ShareButton(row=4))

    @discord.ui.button(
        label="← Back to catalog",
        style=discord.ButtonStyle.primary,
        row=1,
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_catalog_list_payload(self.type_val, page=self.page)
        if payload is None:
            await interaction.response.edit_message(
                content="The catalog is now empty.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ItemsDatabase(bot))
