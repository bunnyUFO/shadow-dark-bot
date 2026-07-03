import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import (
    build_treasury_info_embed,
    build_treasury_instance_embed,
    build_treasury_list_embed,
)
from shadowdark_bot.models import (
    ITEM_TYPE_MAGICAL,
    Borrow,
    CharacterItem,
    Item,
    Location,
    PlayerCharacter,
    TreasuryEntry,
)
from shadowdark_bot.sharing import ShareButton, ShareableView

log = logging.getLogger("shadowdark_bot.treasury")

STATUS_AVAILABLE = "available"
STATUS_BORROWED = "borrowed"
VIEW_TIMEOUT_SECONDS = 300


class MagicalTreasury(commands.Cog):
    """Commands for managing the guild's magical item treasury."""

    treasury = app_commands.Group(
        name="treasury",
        description="Manage the guild's magical treasury",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ---------- add / remove ----------

    @treasury.command(name="add", description="Register a magical item in the treasury")
    @app_commands.describe(
        item="Magical catalog item to register",
        tag="Optional distinguisher for duplicates (e.g., 'chipped')",
    )
    async def add(
        self,
        interaction: discord.Interaction,
        item: str,
        tag: str | None = None,
    ) -> None:
        clean_item = item.strip()
        clean_tag = tag.strip() if tag else None
        failure = f"**Failed to add {clean_item} to treasury.**"

        with session_scope() as session:
            cat_item = session.scalar(select(Item).where(Item.name == clean_item))
            if cat_item is None:
                await interaction.response.send_message(
                    f"{failure}\nNo catalog entry named **{clean_item}**.",
                    ephemeral=True,
                )
                return
            if cat_item.item_type != ITEM_TYPE_MAGICAL:
                await interaction.response.send_message(
                    f"{failure}\n**{clean_item}** is not magical — non-magical items go in inventory.",
                    ephemeral=True,
                )
                return

            loc = _get_or_create_treasury_location(session)

            entry = TreasuryEntry(
                location_id=loc.id,
                item_id=cat_item.id,
                tag=(clean_tag or None),
                status=STATUS_AVAILABLE,
                added_by=str(interaction.user.id),
            )
            session.add(entry)
            session.flush()
            session.refresh(entry, attribute_names=["item"])

            embed = build_treasury_instance_embed(
                entry,
                actor_mention=interaction.user.mention,
                action="added",
            )
            await interaction.response.send_message(
                embed=embed, view=ShareableView(), ephemeral=True
            )

    @treasury.command(name="remove", description="Remove a magical item from the treasury")
    @app_commands.describe(entry_id="Treasury entry to remove (must be available)")
    async def remove(
        self,
        interaction: discord.Interaction,
        entry_id: int,
    ) -> None:
        failure = f"**Failed to remove #{entry_id}.**"
        with session_scope() as session:
            entry = session.get(TreasuryEntry, entry_id)
            if entry is None:
                await interaction.response.send_message(
                    f"{failure}\nNo treasury entry with id {entry_id}.",
                    ephemeral=True,
                )
                return
            if entry.status == STATUS_BORROWED:
                open_borrow = session.scalar(
                    select(Borrow).where(
                        Borrow.treasury_entry_id == entry.id,
                        Borrow.returned_at.is_(None),
                    )
                )
                borrower_str = (
                    f"<@{open_borrow.borrower_id}>" if open_borrow else "someone"
                )
                await interaction.response.send_message(
                    f"{failure}\n#{entry_id} is currently borrowed by {borrower_str}. "
                    "Return it first.",
                    ephemeral=True,
                )
                return

            session.refresh(entry, attribute_names=["item"])
            embed = build_treasury_instance_embed(
                entry,
                actor_mention=interaction.user.mention,
                action="removed",
            )
            session.delete(entry)
            await interaction.response.send_message(
                embed=embed, view=ShareableView(), ephemeral=True
            )

    # ---------- browse ----------

    @treasury.command(
        name="browse",
        description="Browse the treasury: list, inspect, borrow, and return entries",
    )
    @app_commands.describe(status="Filter by status (omit for both)")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="available", value=STATUS_AVAILABLE),
            app_commands.Choice(name="borrowed", value=STATUS_BORROWED),
        ]
    )
    async def browse(
        self,
        interaction: discord.Interaction,
        status: app_commands.Choice[str] | None = None,
    ) -> None:
        status_val = status.value if status else None
        payload = _build_treasury_list_payload(status_val)
        if payload is None:
            msg = (
                f"No {status_val} treasury items."
                if status_val
                else "The treasury is empty. Add one with `/treasury add`."
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return
        embed, view = payload
        await interaction.response.send_message(
            embed=embed, view=view, ephemeral=True
        )

    # ---------- autocompletes ----------

    async def _magical_item_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        with session_scope() as session:
            stmt = (
                select(Item.name)
                .where(
                    Item.item_type == ITEM_TYPE_MAGICAL,
                    Item.name.ilike(f"%{current}%"),
                )
                .order_by(Item.name)
                .limit(25)
            )
            names = list(session.scalars(stmt).all())
        return [app_commands.Choice(name=n, value=n) for n in names]

    @add.autocomplete("item")
    async def _ac_add_item(self, interaction, current):
        return await self._magical_item_autocomplete(interaction, current)

    @remove.autocomplete("entry_id")
    async def _ac_remove_entry(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        with session_scope() as session:
            entries = list(
                session.scalars(
                    select(TreasuryEntry)
                    .options(joinedload(TreasuryEntry.item))
                    .where(TreasuryEntry.status == STATUS_AVAILABLE)
                    .order_by(TreasuryEntry.id)
                ).all()
            )
        choices: list[app_commands.Choice[int]] = []
        for entry in entries:
            label = f"#{entry.id}  {entry.item.name}"
            if entry.tag:
                label += f" ({entry.tag})"
            if current and current.lower() not in label.lower():
                continue
            choices.append(
                app_commands.Choice(name=label[:100], value=entry.id)
            )
            if len(choices) >= 25:
                break
        return choices


def _build_treasury_list_payload(
    status_filter: str | None,
) -> tuple[discord.Embed, "TreasuryListView"] | None:
    """Build (embed, view) for the treasury list. Returns None if no entries
    match the filter."""
    with session_scope() as session:
        available: list[TreasuryEntry] = []
        borrowed: list[tuple[TreasuryEntry, Borrow | None]] = []

        if status_filter != STATUS_BORROWED:
            available = list(
                session.scalars(
                    select(TreasuryEntry)
                    .options(joinedload(TreasuryEntry.item))
                    .where(TreasuryEntry.status == STATUS_AVAILABLE)
                    .order_by(TreasuryEntry.id)
                ).all()
            )

        if status_filter != STATUS_AVAILABLE:
            borrowed_entries = list(
                session.scalars(
                    select(TreasuryEntry)
                    .options(joinedload(TreasuryEntry.item))
                    .where(TreasuryEntry.status == STATUS_BORROWED)
                    .order_by(TreasuryEntry.id)
                ).all()
            )
            for entry in borrowed_entries:
                open_borrow = session.scalar(
                    select(Borrow).where(
                        Borrow.treasury_entry_id == entry.id,
                        Borrow.returned_at.is_(None),
                    )
                )
                borrowed.append((entry, open_borrow))

        if not available and not borrowed:
            return None

        embed = build_treasury_list_embed(available, borrowed, status_filter)

        options: list[tuple[int, str]] = []
        for entry in available:
            label = f"#{entry.id}  {entry.item.name}"
            if entry.tag:
                label += f" ({entry.tag})"
            label += " — Available"
            options.append((entry.id, label))
        for entry, _ob in borrowed:
            label = f"#{entry.id}  {entry.item.name}"
            if entry.tag:
                label += f" ({entry.tag})"
            label += " — Borrowed"
            options.append((entry.id, label))

    return embed, TreasuryListView(options)


def _build_entry_detail_payload(
    entry_id: int,
) -> tuple[discord.Embed, "TreasuryEntryDetailView"] | None:
    """Build (embed, view) for a single treasury entry, reached via the
    /treasury browse dropdown."""
    with session_scope() as session:
        entry = session.get(TreasuryEntry, entry_id)
        if entry is None:
            return None
        session.refresh(entry, attribute_names=["item"])
        open_borrow = None
        if entry.status == STATUS_BORROWED:
            open_borrow = session.scalar(
                select(Borrow).where(
                    Borrow.treasury_entry_id == entry.id,
                    Borrow.returned_at.is_(None),
                )
            )
        embed = build_treasury_info_embed(entry, open_borrow)
        status = entry.status
    return embed, TreasuryEntryDetailView(entry_id, status)


def _naive_utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _add_borrowed_to_inventory(
    session: Session, borrower_id: str, item_id: int
) -> None:
    """If the borrower has a character, add one of the borrowed catalog item to
    their carried inventory (merging into an existing stack). No-op otherwise —
    the borrow is still recorded in the ledger either way."""
    char = session.scalar(
        select(PlayerCharacter).where(PlayerCharacter.user_id == borrower_id)
    )
    if char is None:
        return
    stack = session.scalar(
        select(CharacterItem).where(
            CharacterItem.character_id == char.id,
            CharacterItem.item_id == item_id,
        )
    )
    if stack is None:
        session.add(
            CharacterItem(character_id=char.id, item_id=item_id, quantity=1)
        )
    else:
        stack.quantity += 1
    char.updated_at = _naive_utcnow()


def _remove_borrowed_from_inventory(
    session: Session, borrower_id: str, item_id: int
) -> None:
    """If the borrower has a character carrying the returned catalog item, drop
    one from their inventory (removing the stack when it empties)."""
    char = session.scalar(
        select(PlayerCharacter).where(PlayerCharacter.user_id == borrower_id)
    )
    if char is None:
        return
    stack = session.scalar(
        select(CharacterItem).where(
            CharacterItem.character_id == char.id,
            CharacterItem.item_id == item_id,
        )
    )
    if stack is None:
        return
    if stack.quantity <= 1:
        session.delete(stack)
    else:
        stack.quantity -= 1
    char.updated_at = _naive_utcnow()


async def _do_borrow(
    interaction: discord.Interaction,
    entry_id: int,
    borrower: discord.Member | discord.User,
) -> None:
    """Mark a treasury entry as borrowed and refresh the ephemeral detail view."""
    failure = f"**Failed to borrow #{entry_id}.**"
    with session_scope() as session:
        entry = session.get(TreasuryEntry, entry_id)
        if entry is None:
            await interaction.response.send_message(
                f"{failure}\nNo treasury entry with id {entry_id}.", ephemeral=True
            )
            return
        session.refresh(entry, attribute_names=["item"])
        if entry.status == STATUS_BORROWED:
            open_borrow = session.scalar(
                select(Borrow).where(
                    Borrow.treasury_entry_id == entry.id,
                    Borrow.returned_at.is_(None),
                )
            )
            current_borrower = (
                f"<@{open_borrow.borrower_id}>" if open_borrow else "someone"
            )
            await interaction.response.send_message(
                f"{failure}\n#{entry_id} is already borrowed by {current_borrower}.",
                ephemeral=True,
            )
            return

        entry.status = STATUS_BORROWED
        session.add(
            Borrow(
                treasury_entry_id=entry.id,
                borrower_id=str(borrower.id),
            )
        )
        # If the borrower has a character, mirror the item into their carried
        # inventory. Either way the borrow is recorded above.
        _add_borrowed_to_inventory(session, str(borrower.id), entry.item_id)
        session.flush()

    detail = _build_entry_detail_payload(entry_id)
    if detail is not None:
        new_embed, new_view = detail
        await interaction.response.edit_message(embed=new_embed, view=new_view)


async def _do_return(interaction: discord.Interaction, entry_id: int) -> None:
    """Mark a treasury entry as returned and refresh the ephemeral detail view."""
    failure = f"**Failed to return #{entry_id}.**"
    with session_scope() as session:
        entry = session.get(TreasuryEntry, entry_id)
        if entry is None:
            await interaction.response.send_message(
                f"{failure}\nNo treasury entry with id {entry_id}.", ephemeral=True
            )
            return
        if entry.status != STATUS_BORROWED:
            await interaction.response.send_message(
                f"{failure}\n#{entry_id} isn't currently borrowed.", ephemeral=True
            )
            return
        open_borrow = session.scalar(
            select(Borrow).where(
                Borrow.treasury_entry_id == entry.id,
                Borrow.returned_at.is_(None),
            )
        )
        if open_borrow is None:
            await interaction.response.send_message(
                f"{failure}\n#{entry_id} has no open borrow record.", ephemeral=True
            )
            return

        open_borrow.returned_at = _naive_utcnow()
        entry.status = STATUS_AVAILABLE
        # Mirror the return into the borrower's character inventory, if any.
        _remove_borrowed_from_inventory(session, open_borrow.borrower_id, entry.item_id)

    detail = _build_entry_detail_payload(entry_id)
    if detail is not None:
        new_embed, new_view = detail
        await interaction.response.edit_message(embed=new_embed, view=new_view)


class TreasuryEntrySelect(discord.ui.Select):
    """Dropdown listing treasury entries. Picking one drills into its detail."""

    def __init__(self, entries: list[tuple[int, str]]) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=str(entry_id))
            for entry_id, label in entries[:25]
        ]
        super().__init__(
            placeholder="Inspect an entry…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        entry_id = int(self.values[0])
        detail = _build_entry_detail_payload(entry_id)
        if detail is None:
            await interaction.response.send_message(
                f"Treasury entry #{entry_id} no longer exists.", ephemeral=True
            )
            return
        embed, view = detail
        await interaction.response.edit_message(embed=embed, view=view)


class TreasuryListView(discord.ui.View):
    """Treasury list embed with dropdown + share."""

    def __init__(self, entries: list[tuple[int, str]]) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        if entries:
            self.add_item(TreasuryEntrySelect(entries))
        self.add_item(ShareButton(row=4))


class BorrowSelfButton(discord.ui.Button):
    def __init__(self, entry_id: int) -> None:
        super().__init__(
            label="Borrow for me", style=discord.ButtonStyle.success, row=0
        )
        self.entry_id = entry_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await _do_borrow(interaction, self.entry_id, interaction.user)


class BorrowForUserSelect(discord.ui.UserSelect):
    def __init__(self, entry_id: int) -> None:
        super().__init__(
            placeholder="Borrow for someone…",
            min_values=1,
            max_values=1,
            row=1,
        )
        self.entry_id = entry_id

    async def callback(self, interaction: discord.Interaction) -> None:
        member = self.values[0]
        await _do_borrow(interaction, self.entry_id, member)


class ReturnButton(discord.ui.Button):
    def __init__(self, entry_id: int) -> None:
        super().__init__(
            label="Return", style=discord.ButtonStyle.success, row=0
        )
        self.entry_id = entry_id

    async def callback(self, interaction: discord.Interaction) -> None:
        await _do_return(interaction, self.entry_id)


class BackToTreasuryListButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(
            label="← Back to treasury",
            style=discord.ButtonStyle.primary,
            row=2,
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        payload = _build_treasury_list_payload(None)
        if payload is None:
            await interaction.response.edit_message(
                content="The treasury is now empty.",
                embed=None,
                view=None,
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view, content=None)


class TreasuryEntryDetailView(discord.ui.View):
    """Detail view for one treasury entry: borrow/return controls + back/share."""

    def __init__(self, entry_id: int, status: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        if status == STATUS_AVAILABLE:
            self.add_item(BorrowSelfButton(entry_id))
            self.add_item(BorrowForUserSelect(entry_id))
        else:
            self.add_item(ReturnButton(entry_id))
        self.add_item(BackToTreasuryListButton())
        self.add_item(ShareButton(row=4))


def _get_or_create_treasury_location(session: Session) -> Location:
    """Auto-create the singleton treasury location on first use."""
    loc = session.scalars(
        select(Location).where(Location.kind == "treasury").limit(1)
    ).first()
    if loc is None:
        loc = Location(name="Treasury", kind="treasury", max_gear_slots=0)
        session.add(loc)
        session.flush()
    return loc


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MagicalTreasury(bot))
