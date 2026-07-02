"""`/spells browse` — read-only browser for the built-in Shadow Dark spell
reference, with class / tier / alignment filters.

Independent of characters: anyone can look up spells. Mirrors the `/items
browse` pattern — a paginated list embed, an "Inspect a spell…" dropdown, and
a Share button.
"""

from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import CHARACTER_COLOR, build_spell_embed
from shadowdark_bot.models import CharacterSpell, PlayerCharacter, Spell
from shadowdark_bot.sharing import ShareButton

PAGE_SIZE = 25  # Discord caps a string-select at 25 options
VIEW_TIMEOUT_SECONDS = 300

_ALIGNMENT_NAMES = {"L": "Lawful", "N": "Neutral", "C": "Chaotic"}

# The spellcasting stat determines which spell list a character draws from:
# Wizards cast with INT, Priests with WIS. (No alignment gating.)
_SPELL_CLASS_BY_ABILITY = {"int": "wizard", "wis": "priest"}


def _load_owner(session, owner_id: str) -> PlayerCharacter | None:
    return session.scalars(
        select(PlayerCharacter)
        .options(selectinload(PlayerCharacter.spells))
        .where(PlayerCharacter.user_id == owner_id)
    ).first()


def build_manage_spells_payload(
    owner_id: str,
) -> tuple[discord.Embed, "SpellBrowseView"] | None:
    """Entry point used by /character's Manage Spells button: opens the browser
    in character-management context (Learn/Forget buttons on spell details)."""
    return _build_list_payload(None, None, None, page=0, owner_id=owner_id)


def _filter_stmt(spell_class: str | None, tier: int | None, alignment: str | None):
    stmt = select(Spell)
    if spell_class is not None:
        stmt = stmt.where(Spell.classes.contains(spell_class))
    if tier is not None:
        stmt = stmt.where(Spell.tier == tier)
    if alignment is not None:
        stmt = stmt.where(Spell.alignment == alignment)
    return stmt.order_by(Spell.tier, Spell.name)


def _filter_summary(spell_class: str | None, tier: int | None, alignment: str | None) -> str:
    parts = [
        spell_class.capitalize() if spell_class else "All classes",
        f"Tier {tier}" if tier is not None else "All tiers",
    ]
    if alignment is not None:
        parts.append(_ALIGNMENT_NAMES.get(alignment, alignment))
    return " · ".join(parts)


def _spell_line(sp: Spell) -> str:
    classes = "/".join(c.capitalize() for c in sp.class_list)
    tag = f" · {_ALIGNMENT_NAMES[sp.alignment]}" if sp.alignment else ""
    return f"• **{sp.name}** — T{sp.tier} · {classes}{tag}"


def _build_list_payload(
    spell_class: str | None,
    tier: int | None,
    alignment: str | None,
    page: int = 0,
    owner_id: str | None = None,
) -> tuple[discord.Embed, "SpellBrowseView"] | None:
    """(embed, view) for the filtered spell list, page-sliced. None if empty.

    When `owner_id` is set, the browser runs in character-management context and
    spell details gain Learn/Forget buttons."""
    with session_scope() as session:
        all_spells = list(session.scalars(_filter_stmt(spell_class, tier, alignment)).all())
        if not all_spells:
            return None
        total = len(all_spells)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        page_spells = all_spells[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE]

        title = f"Spells — {_filter_summary(spell_class, tier, alignment)} ({total})"
        embed = discord.Embed(
            title=title,
            description="\n".join(_spell_line(sp) for sp in page_spells),
            color=CHARACTER_COLOR,
        )
        footer = f"Page {page + 1}/{total_pages}" if total_pages > 1 else ""
        if owner_id is not None:
            footer = (footer + "  •  " if footer else "") + "Inspect a spell to learn it"
        if footer:
            embed.set_footer(text=footer)
        names = [sp.name for sp in page_spells]
    return embed, SpellBrowseView(
        spell_class, tier, alignment, names, page, total_pages, owner_id
    )


def _build_detail_payload(
    name: str,
    spell_class: str | None,
    tier: int | None,
    alignment: str | None,
    page: int,
    owner_id: str | None = None,
) -> tuple[discord.Embed, "SpellRefDetailView"] | None:
    with session_scope() as session:
        sp = session.scalar(select(Spell).where(Spell.name == name))
        if sp is None:
            return None
        embed = build_spell_embed(sp)
        known = False
        if owner_id is not None:
            char = _load_owner(session, owner_id)
            if char is not None:
                known = any(s.spell_id == sp.id for s in char.spells)
    return embed, SpellRefDetailView(
        spell_class, tier, alignment, page, owner_id, sp.name, known
    )


class SpellBrowseSelect(discord.ui.Select):
    def __init__(
        self,
        spell_class: str | None,
        tier: int | None,
        alignment: str | None,
        names: list[str],
    ) -> None:
        options = [
            discord.SelectOption(label=n[:100], value=n[:100]) for n in names[:PAGE_SIZE]
        ]
        super().__init__(
            placeholder="Inspect a spell…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.spell_class = spell_class
        self.tier = tier
        self.alignment = alignment

    async def callback(self, interaction: discord.Interaction) -> None:
        view: SpellBrowseView = self.view  # type: ignore[assignment]
        payload = _build_detail_payload(
            self.values[0],
            self.spell_class,
            self.tier,
            self.alignment,
            view.page,
            owner_id=view.owner_id,
        )
        if payload is None:
            await interaction.response.send_message(
                f"**{self.values[0]}** no longer exists.", ephemeral=True
            )
            return
        embed, detail_view = payload
        await interaction.response.edit_message(embed=embed, view=detail_view)


class _SpellPageButton(discord.ui.Button):
    def __init__(self, *, label: str, delta: int, disabled: bool) -> None:
        super().__init__(
            label=label, style=discord.ButtonStyle.secondary, row=1, disabled=disabled
        )
        self.delta = delta

    async def callback(self, interaction: discord.Interaction) -> None:
        view: SpellBrowseView = self.view  # type: ignore[assignment]
        payload = _build_list_payload(
            view.spell_class,
            view.tier,
            view.alignment,
            page=view.page + self.delta,
            owner_id=view.owner_id,
        )
        if payload is None:
            await interaction.response.edit_message(
                content="No spells match.", embed=None, view=None
            )
            return
        embed, new_view = payload
        await interaction.response.edit_message(embed=embed, view=new_view)


class _BackToSheetButton(discord.ui.Button):
    """Returns from the character-management browser to the character sheet."""

    def __init__(self, owner_id: str) -> None:
        super().__init__(
            label="← Back to character", style=discord.ButtonStyle.primary, row=2
        )
        self.owner_id = owner_id

    async def callback(self, interaction: discord.Interaction) -> None:
        # Lazy import avoids a player_characters <-> spell_reference import cycle.
        from shadowdark_bot.cogs.player_characters import _build_sheet_payload

        payload = _build_sheet_payload(self.owner_id)
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class SpellBrowseView(discord.ui.View):
    def __init__(
        self,
        spell_class: str | None,
        tier: int | None,
        alignment: str | None,
        names: list[str],
        page: int,
        total_pages: int,
        owner_id: str | None = None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.spell_class = spell_class
        self.tier = tier
        self.alignment = alignment
        self.page = page
        self.total_pages = total_pages
        self.owner_id = owner_id
        if names:
            self.add_item(SpellBrowseSelect(spell_class, tier, alignment, names))
        if total_pages > 1:
            self.add_item(
                _SpellPageButton(label="← Prev", delta=-1, disabled=(page == 0))
            )
            self.add_item(
                _SpellPageButton(
                    label="Next →", delta=1, disabled=(page >= total_pages - 1)
                )
            )
        if owner_id is not None:
            self.add_item(_BackToSheetButton(owner_id))
        self.add_item(ShareButton(row=4))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and str(interaction.user.id) != self.owner_id:
            await interaction.response.send_message(
                "This isn't your character.", ephemeral=True
            )
            return False
        return True


async def _refresh_detail(
    interaction: discord.Interaction, view: "SpellRefDetailView", confirmation: str
) -> None:
    payload = _build_detail_payload(
        view.spell_name,
        view.spell_class,
        view.tier,
        view.alignment,
        view.page,
        owner_id=view.owner_id,
    )
    if payload is not None:
        embed, new_view = payload
        await interaction.response.edit_message(embed=embed, view=new_view)
    else:
        await interaction.response.defer()
    await interaction.followup.send(confirmation, ephemeral=True)


async def _do_learn(interaction: discord.Interaction, view: "SpellRefDetailView") -> None:
    name = view.spell_name
    failure = f"**Failed to learn {name}.**"
    with session_scope() as session:
        char = _load_owner(session, view.owner_id)
        if char is None:
            await interaction.response.send_message(
                f"{failure}\nCharacter not found — run `/character sheet`.",
                ephemeral=True,
            )
            return
        cls = _SPELL_CLASS_BY_ABILITY.get(char.spell_ability or "")
        if cls is None:
            await interaction.response.send_message(
                f"{failure}\nYour character isn't a spellcaster — set a "
                "spellcasting stat (INT or WIS) via **Edit Talents & Casting**.",
                ephemeral=True,
            )
            return
        sp = session.scalar(select(Spell).where(Spell.name == name))
        if sp is None:
            await interaction.response.send_message(
                f"{failure}\nThat spell no longer exists.", ephemeral=True
            )
            return
        if cls not in sp.class_list:
            allowed = " / ".join(c.capitalize() for c in sp.class_list)
            await interaction.response.send_message(
                f"{failure}\n**{sp.name}** is a {allowed} spell; your character "
                f"casts {cls} spells.",
                ephemeral=True,
            )
            return
        if any(s.spell_id == sp.id for s in char.spells):
            await interaction.response.send_message(
                f"{failure}\nYou already know **{sp.name}**.", ephemeral=True
            )
            return
        session.add(CharacterSpell(character_id=char.id, spell_id=sp.id))
        char.updated_at = datetime.now(UTC)
        session.flush()

    await _refresh_detail(interaction, view, f"Learned **{name}**.")


async def _do_forget(interaction: discord.Interaction, view: "SpellRefDetailView") -> None:
    name = view.spell_name
    with session_scope() as session:
        char = _load_owner(session, view.owner_id)
        sp = session.scalar(select(Spell).where(Spell.name == name)) if char else None
        cs = (
            next((s for s in char.spells if sp and s.spell_id == sp.id), None)
            if char
            else None
        )
        if cs is None:
            await interaction.response.send_message(
                f"**Failed to forget {name}.**\nYou don't know that spell.",
                ephemeral=True,
            )
            return
        session.delete(cs)
        char.updated_at = datetime.now(UTC)

    await _refresh_detail(interaction, view, f"Forgot **{name}**.")


class _LearnButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Learn", style=discord.ButtonStyle.success, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _do_learn(interaction, self.view)  # type: ignore[arg-type]


class _ForgetButton(discord.ui.Button):
    def __init__(self) -> None:
        super().__init__(label="Forget", style=discord.ButtonStyle.danger, row=0)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _do_forget(interaction, self.view)  # type: ignore[arg-type]


class SpellRefDetailView(discord.ui.View):
    def __init__(
        self,
        spell_class: str | None,
        tier: int | None,
        alignment: str | None,
        page: int,
        owner_id: str | None = None,
        spell_name: str | None = None,
        known: bool = False,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.spell_class = spell_class
        self.tier = tier
        self.alignment = alignment
        self.page = page
        self.owner_id = owner_id
        self.spell_name = spell_name
        if owner_id is not None:
            self.add_item(_ForgetButton() if known else _LearnButton())
        self.add_item(ShareButton(row=4))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is not None and str(interaction.user.id) != self.owner_id:
            await interaction.response.send_message(
                "This isn't your character.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(
        label="← Back to list", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_list_payload(
            self.spell_class, self.tier, self.alignment, page=self.page,
            owner_id=self.owner_id,
        )
        if payload is None:
            await interaction.response.edit_message(
                content="No spells match.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class SpellReference(commands.Cog):
    """Read-only browser for the built-in spell reference."""

    spells = app_commands.Group(
        name="spells", description="Browse the Shadow Dark spell reference"
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @spells.command(name="browse", description="Browse spells with optional filters")
    @app_commands.describe(
        spell_class="Filter by class",
        tier="Filter by tier",
        alignment="Filter by alignment (alignment-gated wizard spells)",
    )
    @app_commands.choices(
        spell_class=[
            app_commands.Choice(name="Wizard", value="wizard"),
            app_commands.Choice(name="Priest", value="priest"),
        ],
        tier=[
            app_commands.Choice(name="Tier 1", value=1),
            app_commands.Choice(name="Tier 2", value=2),
            app_commands.Choice(name="Tier 3", value=3),
            app_commands.Choice(name="Tier 4", value=4),
            app_commands.Choice(name="Tier 5", value=5),
        ],
        alignment=[
            app_commands.Choice(name="All", value="all"),
            app_commands.Choice(name="Neutral", value="N"),
            app_commands.Choice(name="Lawful", value="L"),
            app_commands.Choice(name="Chaotic", value="C"),
        ],
    )
    async def browse(
        self,
        interaction: discord.Interaction,
        spell_class: str | None = None,
        tier: int | None = None,
        alignment: str | None = None,
    ) -> None:
        align = None if alignment in (None, "all") else alignment
        payload = _build_list_payload(spell_class, tier, align, page=0)
        if payload is None:
            await interaction.response.send_message(
                f"No spells match ({_filter_summary(spell_class, tier, align)}).",
                ephemeral=True,
            )
            return
        embed, view = payload
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SpellReference(bot))
