"""`/spells browse` — read-only browser for the built-in Shadow Dark spell
reference, with class / tier / alignment filters.

Independent of characters: anyone can look up spells. Mirrors the `/items
browse` pattern — a paginated list embed, an "Inspect a spell…" dropdown, and
a Share button. (Learning spells for a character lives in the `/character`
Manage Spells flow, not here.)
"""

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import CHARACTER_COLOR, build_spell_embed
from shadowdark_bot.models import Spell
from shadowdark_bot.sharing import ShareButton

PAGE_SIZE = 25  # Discord caps a string-select at 25 options
VIEW_TIMEOUT_SECONDS = 300

_ALIGNMENT_NAMES = {"L": "Lawful", "N": "Neutral", "C": "Chaotic"}


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
) -> tuple[discord.Embed, "SpellBrowseView"] | None:
    """(embed, view) for the filtered spell list, page-sliced. None if empty."""
    with session_scope() as session:
        all_spells = list(session.scalars(_filter_stmt(spell_class, tier, alignment)).all())
        if not all_spells:
            return None
        total = len(all_spells)
        total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = max(0, min(page, total_pages - 1))
        page_spells = all_spells[page * PAGE_SIZE : page * PAGE_SIZE + PAGE_SIZE]

        embed = discord.Embed(
            title=f"Spells — {_filter_summary(spell_class, tier, alignment)} ({total})",
            description="\n".join(_spell_line(sp) for sp in page_spells),
            color=CHARACTER_COLOR,
        )
        if total_pages > 1:
            embed.set_footer(text=f"Page {page + 1}/{total_pages}")
        names = [sp.name for sp in page_spells]
    return embed, SpellBrowseView(spell_class, tier, alignment, names, page, total_pages)


def _build_detail_payload(
    name: str,
    spell_class: str | None,
    tier: int | None,
    alignment: str | None,
    page: int,
) -> tuple[discord.Embed, "SpellRefDetailView"] | None:
    with session_scope() as session:
        sp = session.scalar(select(Spell).where(Spell.name == name))
        if sp is None:
            return None
        embed = build_spell_embed(sp)
    return embed, SpellRefDetailView(spell_class, tier, alignment, page)


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
            self.values[0], self.spell_class, self.tier, self.alignment, view.page
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
            view.spell_class, view.tier, view.alignment, page=view.page + self.delta
        )
        if payload is None:
            await interaction.response.edit_message(
                content="No spells match.", embed=None, view=None
            )
            return
        embed, new_view = payload
        await interaction.response.edit_message(embed=embed, view=new_view)


class SpellBrowseView(discord.ui.View):
    def __init__(
        self,
        spell_class: str | None,
        tier: int | None,
        alignment: str | None,
        names: list[str],
        page: int,
        total_pages: int,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.spell_class = spell_class
        self.tier = tier
        self.alignment = alignment
        self.page = page
        self.total_pages = total_pages
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
        self.add_item(ShareButton(row=4))


class SpellRefDetailView(discord.ui.View):
    def __init__(
        self,
        spell_class: str | None,
        tier: int | None,
        alignment: str | None,
        page: int,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.spell_class = spell_class
        self.tier = tier
        self.alignment = alignment
        self.page = page
        self.add_item(ShareButton(row=4))

    @discord.ui.button(
        label="← Back to list", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_list_payload(
            self.spell_class, self.tier, self.alignment, page=self.page
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
