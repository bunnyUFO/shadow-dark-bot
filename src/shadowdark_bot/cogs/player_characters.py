"""`/character` — per-player Shadow Dark character stats + inventory.

One character per Discord user. The owner edits their own sheet through an
ephemeral, interactive embed (buttons + modals); `/character show` renders any
player's sheet read-only. Inventory is hybrid: a carried stack either links to
the shared `/items` catalog (reusing its gear-slot data) or is a freeform typed
item. Carry capacity is the Shadow Dark limit, max(10, STR).
"""

import logging
from collections.abc import Callable

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import Session

from shadowdark_bot import storage
from shadowdark_bot.currency import format_cp, parse_value_string
from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import (
    CHARACTER_COLOR,
    build_character_location_embed,
    build_character_spells_embed,
    build_combat_embed,
    build_inventory_tab_embed,
    build_item_embed,
    build_roleplaying_embed,
    build_spell_embed,
    fmt_slots,
)
from shadowdark_bot.models import (
    LOCATION_ROLE_HELD,
    LOCATION_ROLE_STASH,
    CharacterSpell,
    InventoryEntry,
    Item,
    Location,
    PlayerCharacter,
    Spell,
)
from shadowdark_bot.rules import ABILITIES
from shadowdark_bot.services import characters
from shadowdark_bot.services.characters import CharacterError, CharacterNotFound
from shadowdark_bot.sharing import ShareableView, ShareButton

log = logging.getLogger("shadowdark_bot.characters")

VIEW_TIMEOUT_SECONDS = 300


# ---------- Display helpers ----------


_ALIGNMENT_NAMES = {"L": "Lawful", "N": "Neutral", "C": "Chaotic"}


def _spell_choice_label(spell: Spell) -> str:
    label = spell.name
    if spell.alignment:
        label += f" ({_ALIGNMENT_NAMES.get(spell.alignment, spell.alignment)})"
    return label


# ---------- Payload builders ----------


SHEET_TABS = ("combat", "inventory", "roleplaying")


def _build_tab_embed(
    session: Session, char: PlayerCharacter, tab: str, *, include_stash: bool = True
) -> discord.Embed:
    if tab == "inventory":
        held, stash = storage.ensure_character_locations(session, char)
        held_entries = storage.location_entries(session, held.id)
        stash_entries = (
            storage.location_entries(session, stash.id) if include_stash else None
        )
        return build_inventory_tab_embed(
            char,
            held_entries,
            held.max_gear_slots,
            stash_entries,
            stash.max_gear_slots,
            include_stash=include_stash,
        )
    if tab == "roleplaying":
        return build_roleplaying_embed(char)
    return build_combat_embed(char, characters.sorted_spells(char))


def _build_sheet_payload(
    user_id: str, tab: str = "combat"
) -> tuple[discord.Embed, "CharacterSheetView"] | None:
    with session_scope() as session:
        char = characters.load_character(session, user_id)
        if char is None:
            return None
        embed = _build_tab_embed(session, char, tab)
    return embed, CharacterSheetView(user_id, tab)


def _build_manage_location_payload(
    user_id: str, role: str
) -> tuple[discord.Embed, "CharacterLocationView"] | None:
    with session_scope() as session:
        char = characters.load_character(session, user_id)
        if char is None:
            return None
        held, stash = storage.ensure_character_locations(session, char)
        loc = held if role == LOCATION_ROLE_HELD else stash
        entries = storage.location_entries(session, loc.id)
        embed = build_character_location_embed(loc, entries)
        choices = [(e.id, f"{e.quantity}× {e.display_name}") for e in entries]
    return embed, CharacterLocationView(user_id, role, choices)


def _item_detail_embed(ci: InventoryEntry) -> discord.Embed:
    """Detail embed for one carried stack (shared by owner + read-only views).

    Catalog items reuse the `/items info` renderer (description, gear slots,
    type, value, type color); freeform items show their typed description. Both
    then get a Carrying field with the stack's quantity and slot cost.
    """
    if ci.is_catalog and ci.item is not None:
        embed = build_item_embed(ci.item)
    else:
        embed = discord.Embed(title=ci.display_name, color=CHARACTER_COLOR)
        if ci.notes:
            embed.description = ci.notes

    per = fmt_slots(ci.effective_gear_slots)
    cost = f"{fmt_slots(ci.slot_cost)} ({per} each"
    if ci.effective_bundle_size > 1:
        cost += f", {ci.effective_bundle_size}/slot"
    cost += ")"
    carry_lines = [
        f"**Quantity:** {ci.quantity}",
        f"**Slot cost:** {cost}",
        f"**Source:** {'Catalog' if ci.is_catalog else 'Freeform'}",
    ]
    if ci.is_catalog and ci.notes:
        carry_lines.append(f"_{ci.notes}_")
    embed.add_field(name="Carrying", value="\n".join(carry_lines), inline=False)
    return embed


def _char_spell_detail_embed(cs: CharacterSpell) -> discord.Embed:
    """Detail embed for one known spell (shared by owner + read-only views)."""
    if cs.is_reference and cs.spell is not None:
        return build_spell_embed(cs.spell)
    embed = discord.Embed(title=cs.display_name, color=CHARACTER_COLOR)
    tier = cs.display_tier
    embed.description = (f"**Tier {tier}**\n" if tier else "") + "_(no reference text)_"
    return embed


def _build_item_detail_payload(
    user_id: str, role: str, entry_id: int
) -> tuple[discord.Embed, "CharacterItemDetailView"] | None:
    with session_scope() as session:
        loc = storage.character_location(session, user_id, role)
        if loc is None:
            return None
        entry = session.get(InventoryEntry, entry_id)
        if entry is None or entry.location_id != loc.id:
            return None
        embed = _item_detail_embed(entry)
        name = entry.display_name
        qty = entry.quantity
    return embed, CharacterItemDetailView(user_id, role, entry_id, name, qty)


def _build_show_payload(
    target_id: str, tab: str = "combat"
) -> tuple[discord.Embed, "CharacterShowView"] | None:
    with session_scope() as session:
        char = characters.load_character(session, target_id)
        if char is None:
            return None
        # Show never exposes the stash — held items only.
        embed = _build_tab_embed(session, char, tab, include_stash=False)
        spell_choices: list[tuple[int, str]] = []
        item_choices: list[tuple[int, str]] = []
        if tab == "combat":
            spell_choices = [
                (
                    cs.id,
                    cs.display_name
                    + (f" (T{cs.display_tier})" if cs.display_tier else ""),
                )
                for cs in characters.sorted_spells(char)
            ]
        elif tab == "inventory":
            held = storage.character_location(session, target_id, LOCATION_ROLE_HELD)
            if held is not None:
                item_choices = [
                    (e.id, f"{e.quantity}× {e.display_name}")
                    for e in storage.location_entries(session, held.id)
                ]
    return embed, CharacterShowView(target_id, tab, spell_choices, item_choices)


def _build_show_spell_detail_payload(
    target_id: str, cs_id: int
) -> tuple[discord.Embed, "CharacterShowDetailView"] | None:
    with session_scope() as session:
        char = characters.load_character(session, target_id)
        if char is None:
            return None
        cs = next((s for s in char.spells if s.id == cs_id), None)
        if cs is None:
            return None
        embed = _char_spell_detail_embed(cs)
    return embed, CharacterShowDetailView(target_id, "combat")


def _build_show_item_detail_payload(
    target_id: str, entry_id: int
) -> tuple[discord.Embed, "CharacterShowDetailView"] | None:
    with session_scope() as session:
        held = storage.character_location(session, target_id, LOCATION_ROLE_HELD)
        if held is None:
            return None
        entry = session.get(InventoryEntry, entry_id)
        if entry is None or entry.location_id != held.id:
            return None
        embed = _item_detail_embed(entry)
    return embed, CharacterShowDetailView(target_id, "inventory")


# ---------- Spell management (bespoke, class-limited) ----------


def _build_spells_payload(
    user_id: str,
) -> tuple[discord.Embed, "CharacterSpellsView"] | None:
    """Manage-spells landing: your known spells (with a Forget path) plus a
    Learn button."""
    with session_scope() as session:
        char = characters.load_character(session, user_id)
        if char is None:
            return None
        spells = characters.sorted_spells(char)
        embed = build_character_spells_embed(char, spells)
        is_caster = characters.spell_class_for(char) is not None
        choices = [
            (cs.id, cs.display_name + (f" (T{cs.display_tier})" if cs.display_tier else ""))
            for cs in spells
        ]
    return embed, CharacterSpellsView(user_id, choices, is_caster)


def _build_char_spell_detail_payload(
    user_id: str, cs_id: int
) -> tuple[discord.Embed, "CharacterSpellDetailView"] | None:
    with session_scope() as session:
        char = characters.load_character(session, user_id)
        if char is None:
            return None
        cs = next((s for s in char.spells if s.id == cs_id), None)
        if cs is None:
            return None
        embed = _char_spell_detail_embed(cs)
        name = cs.display_name
    return embed, CharacterSpellDetailView(user_id, cs_id, name)


def _build_learn_tier_payload(
    user_id: str,
) -> tuple[discord.Embed, "LearnTierView"] | None:
    with session_scope() as session:
        char = characters.load_character(session, user_id)
        if char is None:
            return None
        cls = characters.spell_class_for(char)
        if cls is None:
            return None
        addable = characters.addable_spells(session, char, cls)
        tiers = sorted({sp.tier for sp in addable})
        embed = discord.Embed(title="Learn a spell — choose a tier", color=CHARACTER_COLOR)
        if tiers:
            embed.description = (
                f"Choose a tier of **{cls}** spells ({len(addable)} learnable). "
                "Any tier is allowed."
            )
        else:
            embed.description = f"You already know every **{cls}** spell in the reference."
    return embed, LearnTierView(user_id, tiers)


def _build_learn_pick_payload(
    user_id: str, tier: int
) -> tuple[discord.Embed, "LearnPickView"] | None:
    with session_scope() as session:
        char = characters.load_character(session, user_id)
        if char is None:
            return None
        cls = characters.spell_class_for(char)
        if cls is None:
            return None
        choices = [
            (sp.name, _spell_choice_label(sp))
            for sp in characters.addable_spells(session, char, cls, tier)
        ]
        embed = discord.Embed(title=f"Tier {tier} {cls} spells", color=CHARACTER_COLOR)
        embed.description = (
            "Pick a spell to inspect and learn." if choices else "Nothing left to learn here."
        )
    return embed, LearnPickView(user_id, tier, choices)


def _build_learn_detail_payload(
    user_id: str, tier: int, spell_name: str
) -> tuple[discord.Embed, "LearnDetailView"] | None:
    with session_scope() as session:
        sp = session.scalar(select(Spell).where(Spell.name == spell_name))
        if sp is None:
            return None
        embed = build_spell_embed(sp)
    return embed, LearnDetailView(user_id, tier, spell_name)


async def _refresh_sheet(
    interaction: discord.Interaction, user_id: str, tab: str = "combat"
) -> None:
    payload = _build_sheet_payload(user_id, tab)
    if payload is None:
        await interaction.response.edit_message(
            content="Character not found.", embed=None, view=None
        )
        return
    embed, view = payload
    await interaction.response.edit_message(content=None, embed=embed, view=view)


# ---------- Write actions (thin Discord wrappers over services.characters) ----------


async def _fail(interaction: discord.Interaction, header: str, reason: str) -> None:
    await interaction.response.send_message(f"{header}\n{reason}", ephemeral=True)


async def _do_carry(
    interaction: discord.Interaction,
    *,
    user_id: str,
    role: str = LOCATION_ROLE_HELD,
    item_name: str,
    quantity: int,
    freeform_slots: float = 1.0,
    notes: str | None = None,
    edit_view: bool = False,
) -> None:
    failure = f"**Failed to carry {quantity}× {item_name.strip()}.**"
    try:
        with session_scope() as session:
            result = characters.carry(
                session,
                user_id,
                item_name=item_name,
                quantity=quantity,
                role=role,
                freeform_slots=freeform_slots,
                notes=notes,
            )
    except CharacterNotFound:
        await _fail(
            interaction,
            failure,
            "You don't have a character yet — run `/character sheet`.",
        )
        return
    except CharacterError as err:
        await _fail(interaction, failure, str(err))
        return

    confirmation = (
        f"Added {quantity}× **{result.display_name}** to **{result.location_name}**. "
        f"{fmt_slots(result.used_slots)}/{fmt_slots(result.max_slots)} slots used."
    )
    await _respond(interaction, confirmation, user_id, role, edit_view)


async def _do_add_more(
    interaction: discord.Interaction,
    *,
    user_id: str,
    role: str,
    entry_id: int,
    quantity: int,
) -> None:
    try:
        with session_scope() as session:
            result = characters.add_more(
                session, user_id, role=role, entry_id=entry_id, quantity=quantity
            )
    except CharacterError as err:
        await _fail(interaction, "**Failed to add more.**", str(err))
        return

    confirmation = (
        f"Added {quantity}× **{result.display_name}**. "
        f"{fmt_slots(result.used_slots)}/{fmt_slots(result.max_slots)} slots used."
    )
    await _respond(interaction, confirmation, user_id, role, edit_view=True)


async def _do_remove(
    interaction: discord.Interaction,
    *,
    user_id: str,
    role: str,
    entry_id: int,
    quantity: int | None = None,
) -> None:
    """Remove a stack from a character location. `quantity=None` drops the whole
    stack; a value drops that many copies (deleting the stack if it empties)."""
    try:
        with session_scope() as session:
            result = characters.remove(
                session, user_id, role=role, entry_id=entry_id, quantity=quantity
            )
    except CharacterError as err:
        await _fail(interaction, "**Failed to remove.**", str(err))
        return

    if quantity is None:
        confirmation = f"Removed **{result.display_name}**."
    else:
        confirmation = f"Removed {result.removed}× **{result.display_name}**."
    returned = result.returned_treasury_ids
    if returned:
        ids = ", ".join(f"#{eid}" for eid in returned)
        noun = "it" if len(returned) == 1 else "them"
        confirmation += f" Returned {noun} to the treasury ({ids})."
    await _respond(interaction, confirmation, user_id, role, edit_view=True)


# ---------- Give ----------


def _give_targets(
    session: Session, user_id: str, source: Location
) -> list[tuple[int, str]]:
    """(location id, dropdown label) for each valid give destination."""
    targets: list[tuple[int, str]] = []
    for loc in characters.give_target_locations(session, user_id, source):
        if loc.owner_user_id is None:
            label = f"📦 {loc.name}"
        elif loc.owner_user_id == user_id:
            label = (
                "🧍 Your held items"
                if loc.role == LOCATION_ROLE_HELD
                else "🎒 Your stash"
            )
        else:
            label = f"🧑 {loc.name} (held)"
        targets.append((loc.id, label))
    return targets[:25]


def _build_give_payload(
    user_id: str, role: str, entry_id: int
) -> tuple[discord.Embed, "GiveTargetView"] | None:
    with session_scope() as session:
        entry = characters.location_entry(session, user_id, role, entry_id)
        if entry is None:
            return None
        source = session.get(Location, entry.location_id)
        targets = _give_targets(session, user_id, source)
        name = entry.display_name
        qty = entry.quantity
        embed = discord.Embed(title=f"Give {name}", color=CHARACTER_COLOR)
        if targets:
            embed.description = (
                f"You have {qty}× **{name}**. Choose where to give it."
            )
        else:
            embed.description = "There's nowhere valid to give this right now."
    return embed, GiveTargetView(user_id, role, entry_id, name, qty, targets)


async def _do_give(
    interaction: discord.Interaction,
    *,
    user_id: str,
    role: str,
    entry_id: int,
    target_location_id: int,
    quantity: int | None = None,
) -> None:
    try:
        with session_scope() as session:
            result = characters.give(
                session,
                user_id,
                role=role,
                entry_id=entry_id,
                target_location_id=target_location_id,
                quantity=quantity,
            )
    except CharacterError as err:
        await _fail(interaction, "**Failed to give.**", str(err))
        return

    confirmation = (
        f"Gave {result.quantity}× **{result.display_name}** to **{result.target_name}**."
    )
    await _respond(interaction, confirmation, user_id, role, edit_view=True)


async def _respond(
    interaction: discord.Interaction,
    confirmation: str,
    user_id: str,
    role: str,
    edit_view: bool,
) -> None:
    """For interactive (edit_view) actions, refresh the managed location view in
    place and follow up with an ephemeral confirmation; otherwise reply directly."""
    if edit_view:
        payload = _build_manage_location_payload(user_id, role)
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


async def _do_learn_spell(
    interaction: discord.Interaction, *, user_id: str, tier: int, spell_name: str
) -> None:
    try:
        with session_scope() as session:
            characters.learn_spell(session, user_id, spell_name)
    except CharacterError as err:
        await _fail(interaction, f"**Failed to learn {spell_name}.**", str(err))
        return

    # Return to the tier's pick list (the learned spell is now excluded).
    payload = _build_learn_pick_payload(user_id, tier)
    if payload is not None:
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.defer()
    await interaction.followup.send(
        f"Learned **{spell_name}**.", view=ShareableView(), ephemeral=True
    )


async def _do_forget_spell(
    interaction: discord.Interaction, *, user_id: str, cs_id: int
) -> None:
    try:
        with session_scope() as session:
            display = characters.forget_spell(session, user_id, cs_id)
    except CharacterError as err:
        await _fail(interaction, "**Failed to forget.**", str(err))
        return

    payload = _build_spells_payload(user_id)
    if payload is not None:
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)
    else:
        await interaction.response.defer()
    await interaction.followup.send(
        f"Forgot **{display}**.", view=ShareableView(), ephemeral=True
    )


# ---------- Modals ----------


def _modal_tab(modal: discord.ui.Modal, default: str) -> str:
    """Which sheet tab to refresh to after a modal submit (set by the sheet
    view when it opens the modal)."""
    return getattr(modal, "refresh_tab", default)


async def _save_and_refresh(
    interaction: discord.Interaction,
    user_id: str,
    tab: str,
    save: Callable[[Session], object],
) -> None:
    """Run a sheet edit (`save(session)`) in one transaction, then refresh the
    sheet on `tab`. Validation failures become an ephemeral error."""
    try:
        with session_scope() as session:
            save(session)
    except CharacterNotFound:
        await interaction.response.send_message(
            "Character not found — run `/character sheet` first.", ephemeral=True
        )
        return
    except CharacterError as err:
        await interaction.response.send_message(
            f"**Failed to save.**\n{err}", ephemeral=True
        )
        return
    await _refresh_sheet(interaction, user_id, tab)


class EditStatsModal(discord.ui.Modal):
    """Combat-tab stats: the six ability scores, level, max HP, base AC, and the
    spellcasting stat + talent bonus (e.g. INT, WIS +1, or none)."""

    def __init__(
        self,
        user_id: str,
        *,
        scores: str = "",
        level: str = "1",
        max_hp: str = "",
        ac: str = "",
        spell_stat: str = "",
    ) -> None:
        super().__init__(title="Stats")
        self.user_id = user_id
        self._scores = discord.ui.TextInput(
            label="Scores: STR DEX CON INT WIS CHA",
            required=True,
            default=scores,
            max_length=40,
        )
        self._level = discord.ui.TextInput(
            label="Level", required=True, default=level, max_length=3
        )
        self._hp = discord.ui.TextInput(
            label="Max HP (blank = unset)", required=False, default=max_hp, max_length=4
        )
        self._ac = discord.ui.TextInput(
            label="Base AC / armor (DEX added on sheet)",
            required=False,
            default=ac,
            max_length=3,
        )
        self._spell_stat = discord.ui.TextInput(
            label="Spellcasting: INT / WIS +bonus / none",
            required=False,
            default=spell_stat,
            max_length=10,
        )
        for field in (
            self._scores,
            self._level,
            self._hp,
            self._ac,
            self._spell_stat,
        ):
            self.add_item(field)

    @classmethod
    def from_char(cls, char: PlayerCharacter) -> "EditStatsModal":
        scores = " ".join(str(getattr(char, f"{k}_score")) for k, _ in ABILITIES)
        return cls(
            char.user_id,
            scores=scores,
            level=str(char.level),
            max_hp=str(char.max_hp) if char.max_hp is not None else "",
            ac=str(char.armor_class) if char.armor_class is not None else "",
            spell_stat=characters.format_spellcasting(char),
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            scores = characters.parse_scores(str(self._scores.value))
        except ValueError as err:
            await interaction.response.send_message(
                f"**Failed to save.**\n{err}", ephemeral=True
            )
            return
        try:
            level = int(str(self._level.value).strip())
            if level < 1:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "**Failed to save.**\nLevel must be a whole number ≥ 1.", ephemeral=True
            )
            return
        try:
            max_hp = characters.parse_optional_int(str(self._hp.value), minimum=0)
            armor_class = characters.parse_optional_int(str(self._ac.value))
        except ValueError:
            await interaction.response.send_message(
                "**Failed to save.**\nMax HP and AC must be whole numbers.",
                ephemeral=True,
            )
            return
        try:
            spell_ability, spell_bonus = characters.parse_spellcasting(str(self._spell_stat.value))
        except ValueError as err:
            await interaction.response.send_message(
                f"**Failed to save.**\n{err}", ephemeral=True
            )
            return

        await _save_and_refresh(
            interaction,
            self.user_id,
            _modal_tab(self, "combat"),
            lambda session: characters.update_stats(
                session,
                self.user_id,
                scores=scores,
                level=level,
                max_hp=max_hp,
                armor_class=armor_class,
                spell_ability=spell_ability,
                spell_check_bonus=spell_bonus,
            ),
        )


class EditGoldModal(discord.ui.Modal):
    def __init__(self, user_id: str, *, gold: str = "") -> None:
        super().__init__(title="Gold")
        self.user_id = user_id
        self._gold = discord.ui.TextInput(
            label='Gold (e.g. "10gp 5sp"; blank = 0)',
            required=False,
            default=gold,
            max_length=50,
        )
        self.add_item(self._gold)

    @classmethod
    def from_char(cls, char: PlayerCharacter) -> "EditGoldModal":
        return cls(char.user_id, gold=format_cp(char.gold_cp) or "0cp")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            gold = parse_value_string(str(self._gold.value))
        except ValueError as err:
            await interaction.response.send_message(
                f"**Failed to save.**\n{err}", ephemeral=True
            )
            return
        await _save_and_refresh(
            interaction,
            self.user_id,
            _modal_tab(self, "inventory"),
            lambda session: characters.set_gold(session, self.user_id, gold),
        )


class EditProficienciesModal(discord.ui.Modal):
    def __init__(self, user_id: str, *, proficiencies: str = "") -> None:
        super().__init__(title="Proficiencies")
        self.user_id = user_id
        self._prof = discord.ui.TextInput(
            label="Proficiencies",
            required=False,
            default=proficiencies,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.add_item(self._prof)

    @classmethod
    def from_char(cls, char: PlayerCharacter) -> "EditProficienciesModal":
        return cls(char.user_id, proficiencies=char.proficiencies or "")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _save_and_refresh(
            interaction,
            self.user_id,
            _modal_tab(self, "combat"),
            lambda session: characters.set_proficiencies(
                session, self.user_id, str(self._prof.value)
            ),
        )


class EditSkillsKnowledgeModal(discord.ui.Modal):
    """Roleplaying-tab prose: known languages, talents, and additional info."""

    def __init__(
        self,
        user_id: str,
        *,
        languages: str = "",
        talents: str = "",
        additional_info: str = "",
    ) -> None:
        super().__init__(title="Skills & Knowledge")
        self.user_id = user_id
        self._languages = discord.ui.TextInput(
            label="Known languages (comma-separated)",
            required=False,
            default=languages,
            style=discord.TextStyle.paragraph,
            max_length=300,
        )
        self._talents = discord.ui.TextInput(
            label="Talents",
            required=False,
            default=talents,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self._additional_info = discord.ui.TextInput(
            label="Additional info",
            required=False,
            default=additional_info,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self.add_item(self._languages)
        self.add_item(self._talents)
        self.add_item(self._additional_info)

    @classmethod
    def from_char(cls, char: PlayerCharacter) -> "EditSkillsKnowledgeModal":
        return cls(
            char.user_id,
            languages=char.languages or "",
            talents=char.talents or "",
            additional_info=char.additional_info or "",
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await _save_and_refresh(
            interaction,
            self.user_id,
            _modal_tab(self, "roleplaying"),
            lambda session: characters.set_skills_knowledge(
                session,
                self.user_id,
                languages=str(self._languages.value),
                talents=str(self._talents.value),
                additional_info=str(self._additional_info.value),
            ),
        )


class EditIdentityModal(discord.ui.Modal):
    """Identity: name, class/title, ancestry, alignment, background. Upserts —
    this is also the character-creation form (Name required)."""

    def __init__(
        self,
        user_id: str,
        *,
        name: str = "",
        class_title: str = "",
        ancestry: str = "",
        alignment: str = "",
        background: str = "",
    ) -> None:
        super().__init__(title="Identity")
        self.user_id = user_id
        self._name = discord.ui.TextInput(
            label="Name", required=True, default=name, max_length=100
        )
        self._class_title = discord.ui.TextInput(
            label="Class & title (e.g. Wizard, Channeler)",
            required=False,
            default=class_title,
            max_length=100,
        )
        self._ancestry = discord.ui.TextInput(
            label="Ancestry", required=False, default=ancestry, max_length=50
        )
        self._alignment = discord.ui.TextInput(
            label="Alignment (Lawful / Neutral / Chaotic)",
            required=False,
            default=alignment,
            max_length=10,
        )
        self._background = discord.ui.TextInput(
            label="Background",
            required=False,
            default=background,
            style=discord.TextStyle.paragraph,
            max_length=300,
        )
        for field in (
            self._name,
            self._class_title,
            self._ancestry,
            self._alignment,
            self._background,
        ):
            self.add_item(field)

    @classmethod
    def from_char(cls, char: PlayerCharacter) -> "EditIdentityModal":
        class_title = char.char_class or ""
        if char.title:
            class_title = f"{class_title}, {char.title}" if class_title else char.title
        return cls(
            char.user_id,
            name=char.name,
            class_title=class_title,
            ancestry=char.ancestry or "",
            alignment=char.alignment or "",
            background=char.background or "",
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # "Class & title" is one field (modals cap at five inputs).
        char_class, title = characters.parse_class_title(str(self._class_title.value))
        await _save_and_refresh(
            interaction,
            self.user_id,
            _modal_tab(self, "roleplaying"),
            lambda session: characters.save_identity(
                session,
                self.user_id,
                name=str(self._name.value),
                char_class=char_class,
                title=title,
                ancestry=str(self._ancestry.value),
                alignment=str(self._alignment.value),
                background=str(self._background.value),
            ),
        )


class AddItemModal(discord.ui.Modal):
    """Add an item by name to a character location (held or stash). A name
    matching the catalog links to it; otherwise a freeform stack is created with
    the given per-item slot cost."""

    def __init__(self, user_id: str, role: str = LOCATION_ROLE_HELD) -> None:
        super().__init__(title="Add an item")
        self.user_id = user_id
        self.role = role
        self._name = discord.ui.TextInput(
            label="Item name (catalog name links it)",
            required=True,
            max_length=100,
        )
        self._qty = discord.ui.TextInput(
            label="Quantity", required=True, default="1", max_length=8
        )
        self._slots = discord.ui.TextInput(
            label="Gear slots each (freeform; blank = 1)",
            required=False,
            max_length=10,
        )
        self._description = discord.ui.TextInput(
            label="Description (freeform; optional)",
            required=False,
            style=discord.TextStyle.paragraph,
            max_length=500,
        )
        self.add_item(self._name)
        self.add_item(self._qty)
        self.add_item(self._slots)
        self.add_item(self._description)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            quantity = int(str(self._qty.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "**Failed to add.**\nQuantity must be a whole number.", ephemeral=True
            )
            return
        slots_raw = str(self._slots.value).strip()
        try:
            freeform_slots = float(slots_raw) if slots_raw else 1.0
            if freeform_slots < 0:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                "**Failed to add.**\nGear slots each must be a number ≥ 0.",
                ephemeral=True,
            )
            return
        await _do_carry(
            interaction,
            user_id=self.user_id,
            role=self.role,
            item_name=str(self._name.value),
            quantity=quantity,
            freeform_slots=freeform_slots,
            notes=str(self._description.value).strip() or None,
            edit_view=True,
        )


class _QuantityModal(discord.ui.Modal):
    """Single-quantity modal for the item detail Add-more / Remove / Give
    buttons. `target_id` is only used by the Give action."""

    def __init__(
        self,
        title: str,
        user_id: str,
        role: str,
        entry_id: int,
        *,
        action: str = "add",
        target_id: int | None = None,
    ) -> None:
        super().__init__(title=title[:45])
        self.user_id = user_id
        self.role = role
        self.entry_id = entry_id
        self.action = action
        self.target_id = target_id
        self._qty = discord.ui.TextInput(
            label="Quantity", required=True, default="1", max_length=8
        )
        self.add_item(self._qty)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            quantity = int(str(self._qty.value).strip())
        except ValueError:
            await interaction.response.send_message(
                "Quantity must be a whole number.", ephemeral=True
            )
            return
        if self.action == "remove":
            await _do_remove(
                interaction,
                user_id=self.user_id,
                role=self.role,
                entry_id=self.entry_id,
                quantity=quantity,
            )
        elif self.action == "give":
            await _do_give(
                interaction,
                user_id=self.user_id,
                role=self.role,
                entry_id=self.entry_id,
                target_location_id=self.target_id,
                quantity=quantity,
            )
        else:
            await _do_add_more(
                interaction,
                user_id=self.user_id,
                role=self.role,
                entry_id=self.entry_id,
                quantity=quantity,
            )


# ---------- Views ----------


class _OwnerView(discord.ui.View):
    """Base view whose interactions are restricted to the character's owner."""

    def __init__(self, user_id: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.user_id = user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message(
                "This isn't your character sheet.", ephemeral=True
            )
            return False
        return True


class NoCharacterView(_OwnerView):
    @discord.ui.button(label="Create character", style=discord.ButtonStyle.success)
    async def create(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(EditIdentityModal(self.user_id))


class _TabButton(discord.ui.Button):
    """Switches the sheet to another tab (the active tab's button is disabled)."""

    def __init__(self, label: str, tab: str, active: bool) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary
            if active
            else discord.ButtonStyle.primary,
            disabled=active,
            row=0,
        )
        self.tab = tab

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CharacterSheetView = self.view  # type: ignore[assignment]
        payload = _build_sheet_payload(view.user_id, self.tab)
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, new_view = payload
        await interaction.response.edit_message(embed=embed, view=new_view)


class _EditButton(discord.ui.Button):
    """Opens an edit modal, tagging it with the current tab so the sheet
    refreshes back to where the edit was launched from."""

    def __init__(self, label: str, factory, row: int) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=row)
        self.factory = factory

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CharacterSheetView = self.view  # type: ignore[assignment]
        with session_scope() as session:
            char = session.scalar(
                select(PlayerCharacter).where(PlayerCharacter.user_id == view.user_id)
            )
            if char is None:
                await interaction.response.send_message(
                    "Character not found.", ephemeral=True
                )
                return
            modal = self.factory(char)
        modal.refresh_tab = view.tab
        await interaction.response.send_modal(modal)


class _SubViewButton(discord.ui.Button):
    """Swaps to a sub-view (Manage Spells)."""

    def __init__(self, label: str, builder, row: int) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.success, row=row)
        self.builder = builder

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CharacterSheetView = self.view  # type: ignore[assignment]
        payload = self.builder(view.user_id)
        if payload is None:
            await interaction.response.send_message(
                "Character not found.", ephemeral=True
            )
            return
        embed, sub_view = payload
        await interaction.response.edit_message(embed=embed, view=sub_view)


class _ManageLocationButton(discord.ui.Button):
    """Opens the manage view for one of the character's locations (held/stash)."""

    def __init__(self, label: str, role: str, row: int) -> None:
        super().__init__(label=label, style=discord.ButtonStyle.success, row=row)
        self.role = role

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CharacterSheetView = self.view  # type: ignore[assignment]
        payload = _build_manage_location_payload(view.user_id, self.role)
        if payload is None:
            await interaction.response.send_message(
                "Character not found.", ephemeral=True
            )
            return
        embed, sub_view = payload
        await interaction.response.edit_message(embed=embed, view=sub_view)


class CharacterSheetView(_OwnerView):
    """Tabbed character sheet: Combat (home) / Inventory / Roleplaying."""

    def __init__(self, user_id: str, tab: str = "combat") -> None:
        super().__init__(user_id)
        self.tab = tab

        self.add_item(_TabButton("Combat", "combat", tab == "combat"))
        self.add_item(_TabButton("Inventory", "inventory", tab == "inventory"))
        self.add_item(_TabButton("Roleplaying", "roleplaying", tab == "roleplaying"))

        if tab == "combat":
            self.add_item(_EditButton("Edit Stats", EditStatsModal.from_char, 1))
            self.add_item(
                _EditButton("Edit Proficiencies", EditProficienciesModal.from_char, 1)
            )
            self.add_item(_SubViewButton("Manage Spells", _build_spells_payload, 2))
        elif tab == "inventory":
            self.add_item(_ManageLocationButton("Manage Held", LOCATION_ROLE_HELD, 1))
            self.add_item(
                _ManageLocationButton("Manage Stash", LOCATION_ROLE_STASH, 1)
            )
            self.add_item(_EditButton("Edit Gold", EditGoldModal.from_char, 1))
        else:  # roleplaying
            self.add_item(_EditButton("Edit Identity", EditIdentityModal.from_char, 1))
            self.add_item(
                _EditButton(
                    "Edit Skills & Knowledge",
                    EditSkillsKnowledgeModal.from_char,
                    1,
                )
            )
            self.add_item(_DeleteButton(2))

        self.add_item(ShareButton(row=4))


class _DeleteButton(discord.ui.Button):
    def __init__(self, row: int) -> None:
        super().__init__(
            label="Delete character", style=discord.ButtonStyle.danger, row=row
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CharacterSheetView = self.view  # type: ignore[assignment]
        try:
            with session_scope() as session:
                preview = characters.delete_preview(session, view.user_id)
        except CharacterNotFound:
            await interaction.response.send_message(
                "Character not found.", ephemeral=True
            )
            return
        embed = discord.Embed(
            title="Delete character?",
            description=(
                f"Are you sure you want to delete **{preview.name}**? "
                f"This also drops its {preview.stack_count} stored stack(s) "
                "(held + stash) and all known spells, and can't be undone."
            ),
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(
            embed=embed, view=DeleteConfirmView(view.user_id)
        )


class CharacterItemSelect(discord.ui.Select):
    def __init__(
        self, user_id: str, role: str, choices: list[tuple[int, str]]
    ) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=str(cid))
            for cid, label in choices[:25]
        ]
        super().__init__(
            placeholder="Inspect an item…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.user_id = user_id
        self.role = role

    async def callback(self, interaction: discord.Interaction) -> None:
        payload = _build_item_detail_payload(
            self.user_id, self.role, int(self.values[0])
        )
        if payload is None:
            await interaction.response.send_message(
                "That item is no longer there.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class CharacterLocationView(_OwnerView):
    """Manage one of the character's locations (held or stash)."""

    def __init__(
        self, user_id: str, role: str, choices: list[tuple[int, str]]
    ) -> None:
        super().__init__(user_id)
        self.role = role
        if choices:
            self.add_item(CharacterItemSelect(user_id, role, choices))
        self.add_item(ShareButton(row=4))

    @discord.ui.button(label="Add item", style=discord.ButtonStyle.success, row=1)
    async def add_item_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(AddItemModal(self.user_id, self.role))

    @discord.ui.button(
        label="← Back to sheet", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_sheet_payload(self.user_id, "inventory")
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class _AddMoreButton(discord.ui.Button):
    def __init__(self, user_id: str, role: str, entry_id: int, item_name: str) -> None:
        super().__init__(label="+ Add more", style=discord.ButtonStyle.success, row=0)
        self.user_id = user_id
        self.role = role
        self.entry_id = entry_id
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            _QuantityModal(
                f"Add more {self.item_name}",
                self.user_id,
                self.role,
                self.entry_id,
            )
        )


class _RemoveButton(discord.ui.Button):
    """Remove a stack. With more than one copy, prompt for how many to drop;
    with a single copy, remove it outright."""

    def __init__(
        self, user_id: str, role: str, entry_id: int, item_name: str, qty: int
    ) -> None:
        super().__init__(label="Remove", style=discord.ButtonStyle.danger, row=0)
        self.user_id = user_id
        self.role = role
        self.entry_id = entry_id
        self.item_name = item_name
        self.qty = qty

    async def callback(self, interaction: discord.Interaction) -> None:
        if self.qty > 1:
            await interaction.response.send_modal(
                _QuantityModal(
                    f"Remove {self.item_name}",
                    self.user_id,
                    self.role,
                    self.entry_id,
                    action="remove",
                )
            )
        else:
            await _do_remove(
                interaction,
                user_id=self.user_id,
                role=self.role,
                entry_id=self.entry_id,
            )


class _GiveButton(discord.ui.Button):
    """Give this stack to another character or a storage location."""

    def __init__(self, user_id: str, role: str, entry_id: int) -> None:
        super().__init__(label="Give", style=discord.ButtonStyle.primary, row=0)
        self.user_id = user_id
        self.role = role
        self.entry_id = entry_id

    async def callback(self, interaction: discord.Interaction) -> None:
        payload = _build_give_payload(self.user_id, self.role, self.entry_id)
        if payload is None:
            await interaction.response.send_message(
                "That item is no longer there.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class CharacterItemDetailView(_OwnerView):
    def __init__(
        self, user_id: str, role: str, entry_id: int, item_name: str, qty: int
    ) -> None:
        super().__init__(user_id)
        self.role = role
        self.entry_id = entry_id
        self.add_item(_AddMoreButton(user_id, role, entry_id, item_name))
        self.add_item(_RemoveButton(user_id, role, entry_id, item_name, qty))
        self.add_item(_GiveButton(user_id, role, entry_id))
        self.add_item(ShareButton(row=4))

    @discord.ui.button(
        label="← Back", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_manage_location_payload(self.user_id, self.role)
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class GiveTargetSelect(discord.ui.Select):
    def __init__(
        self,
        user_id: str,
        role: str,
        entry_id: int,
        item_name: str,
        qty: int,
        targets: list[tuple[int, str]],
    ) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=str(loc_id))
            for loc_id, label in targets[:25]
        ]
        super().__init__(
            placeholder="Give to…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.user_id = user_id
        self.role = role
        self.entry_id = entry_id
        self.item_name = item_name
        self.qty = qty

    async def callback(self, interaction: discord.Interaction) -> None:
        target_id = int(self.values[0])
        if self.qty > 1:
            await interaction.response.send_modal(
                _QuantityModal(
                    f"Give {self.item_name}",
                    self.user_id,
                    self.role,
                    self.entry_id,
                    action="give",
                    target_id=target_id,
                )
            )
        else:
            await _do_give(
                interaction,
                user_id=self.user_id,
                role=self.role,
                entry_id=self.entry_id,
                target_location_id=target_id,
                quantity=1,
            )


class GiveTargetView(_OwnerView):
    def __init__(
        self,
        user_id: str,
        role: str,
        entry_id: int,
        item_name: str,
        qty: int,
        targets: list[tuple[int, str]],
    ) -> None:
        super().__init__(user_id)
        self.role = role
        self.entry_id = entry_id
        if targets:
            self.add_item(
                GiveTargetSelect(user_id, role, entry_id, item_name, qty, targets)
            )
        self.add_item(ShareButton(row=4))

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.primary, row=1)
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_item_detail_payload(self.user_id, self.role, self.entry_id)
        if payload is None:
            payload = _build_manage_location_payload(self.user_id, self.role)
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class CharacterSpellSelect(discord.ui.Select):
    def __init__(self, user_id: str, choices: list[tuple[int, str]]) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=str(cid))
            for cid, label in choices[:25]
        ]
        super().__init__(
            placeholder="View a known spell…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        payload = _build_char_spell_detail_payload(self.user_id, int(self.values[0]))
        if payload is None:
            await interaction.response.send_message(
                "You no longer know that spell.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class CharacterSpellsView(_OwnerView):
    """Manage-spells landing: known-spell dropdown + Learn + Back to sheet."""

    def __init__(
        self, user_id: str, choices: list[tuple[int, str]], is_caster: bool
    ) -> None:
        super().__init__(user_id)
        self.is_caster = is_caster
        if choices:
            self.add_item(CharacterSpellSelect(user_id, choices))
        self.add_item(ShareButton(row=4))

    @discord.ui.button(label="Learn a spell", style=discord.ButtonStyle.success, row=1)
    async def learn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        if not self.is_caster:
            await interaction.response.send_message(
                "Your character isn't a spellcaster — set a spellcasting stat "
                "(INT or WIS) via **Edit Stats** first.",
                ephemeral=True,
            )
            return
        payload = _build_learn_tier_payload(self.user_id)
        if payload is None:
            await interaction.response.send_message(
                "Character not found.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(
        label="← Back to sheet", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_sheet_payload(self.user_id)
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class CharacterSpellDetailView(_OwnerView):
    """A known spell's detail with a Forget button."""

    def __init__(self, user_id: str, cs_id: int, spell_name: str) -> None:
        super().__init__(user_id)
        self.cs_id = cs_id
        self.add_item(ShareButton(row=4))

    @discord.ui.button(label="Forget", style=discord.ButtonStyle.danger, row=0)
    async def forget(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _do_forget_spell(interaction, user_id=self.user_id, cs_id=self.cs_id)

    @discord.ui.button(
        label="← Back to spells", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_spells_payload(self.user_id)
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class LearnTierSelect(discord.ui.Select):
    def __init__(self, user_id: str, tiers: list[int]) -> None:
        options = [
            discord.SelectOption(label=f"Tier {t}", value=str(t)) for t in tiers[:25]
        ]
        super().__init__(
            placeholder="Choose a tier…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        payload = _build_learn_pick_payload(self.user_id, int(self.values[0]))
        if payload is None:
            await interaction.response.send_message(
                "Character not found.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class LearnTierView(_OwnerView):
    def __init__(self, user_id: str, tiers: list[int]) -> None:
        super().__init__(user_id)
        if tiers:
            self.add_item(LearnTierSelect(user_id, tiers))

    @discord.ui.button(
        label="← Back to spells", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_spells_payload(self.user_id)
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class LearnPickSelect(discord.ui.Select):
    def __init__(self, user_id: str, tier: int, choices: list[tuple[str, str]]) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=name[:100])
            for name, label in choices[:25]
        ]
        super().__init__(
            placeholder="Choose a spell…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.user_id = user_id
        self.tier = tier

    async def callback(self, interaction: discord.Interaction) -> None:
        payload = _build_learn_detail_payload(self.user_id, self.tier, self.values[0])
        if payload is None:
            await interaction.response.send_message(
                "That spell no longer exists.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class LearnPickView(_OwnerView):
    def __init__(
        self, user_id: str, tier: int, choices: list[tuple[str, str]]
    ) -> None:
        super().__init__(user_id)
        self.tier = tier
        if choices:
            self.add_item(LearnPickSelect(user_id, tier, choices))

    @discord.ui.button(
        label="← Back to tiers", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_learn_tier_payload(self.user_id)
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class LearnDetailView(_OwnerView):
    """A learnable spell's detail with a Learn button (class-checked on press)."""

    def __init__(self, user_id: str, tier: int, spell_name: str) -> None:
        super().__init__(user_id)
        self.tier = tier
        self.spell_name = spell_name
        self.add_item(ShareButton(row=4))

    @discord.ui.button(label="Learn", style=discord.ButtonStyle.success, row=0)
    async def learn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _do_learn_spell(
            interaction, user_id=self.user_id, tier=self.tier, spell_name=self.spell_name
        )

    @discord.ui.button(
        label="← Back to list", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_learn_pick_payload(self.user_id, self.tier)
        if payload is None:
            await interaction.response.edit_message(
                content="Character not found.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class _ShowTabButton(discord.ui.Button):
    def __init__(self, label: str, tab: str, active: bool) -> None:
        super().__init__(
            label=label,
            style=discord.ButtonStyle.secondary
            if active
            else discord.ButtonStyle.primary,
            disabled=active,
            row=0,
        )
        self.tab = tab

    async def callback(self, interaction: discord.Interaction) -> None:
        view: CharacterShowView = self.view  # type: ignore[assignment]
        payload = _build_show_payload(view.target_id, self.tab)
        if payload is None:
            await interaction.response.edit_message(
                content="That character no longer exists.", embed=None, view=None
            )
            return
        embed, new_view = payload
        await interaction.response.edit_message(embed=embed, view=new_view)


class ShowSpellSelect(discord.ui.Select):
    """Read-only spell inspector on another player's Combat tab."""

    def __init__(self, target_id: str, choices: list[tuple[int, str]]) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=str(cid))
            for cid, label in choices[:25]
        ]
        super().__init__(
            placeholder="View a known spell…",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
        )
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction) -> None:
        payload = _build_show_spell_detail_payload(self.target_id, int(self.values[0]))
        if payload is None:
            await interaction.response.send_message(
                "That spell is no longer known.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class ShowItemSelect(discord.ui.Select):
    """Read-only item inspector on another player's Inventory tab."""

    def __init__(self, target_id: str, choices: list[tuple[int, str]]) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=str(cid))
            for cid, label in choices[:25]
        ]
        super().__init__(
            placeholder="Inspect a carried item…",
            options=options,
            min_values=1,
            max_values=1,
            row=1,
        )
        self.target_id = target_id

    async def callback(self, interaction: discord.Interaction) -> None:
        payload = _build_show_item_detail_payload(self.target_id, int(self.values[0]))
        if payload is None:
            await interaction.response.send_message(
                "That item is no longer carried.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class CharacterShowView(discord.ui.View):
    """Read-only view of another player's sheet — Combat / Inventory /
    Roleplaying tabs + share. Combat and Inventory tabs offer a read-only
    dropdown to inspect a spell or item in place. No edit controls."""

    def __init__(
        self,
        target_id: str,
        tab: str = "combat",
        spell_choices: list[tuple[int, str]] | None = None,
        item_choices: list[tuple[int, str]] | None = None,
    ) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.target_id = target_id
        self.tab = tab
        self.add_item(_ShowTabButton("Combat", "combat", tab == "combat"))
        self.add_item(_ShowTabButton("Inventory", "inventory", tab == "inventory"))
        self.add_item(
            _ShowTabButton("Roleplaying", "roleplaying", tab == "roleplaying")
        )
        if tab == "combat" and spell_choices:
            self.add_item(ShowSpellSelect(target_id, spell_choices))
        elif tab == "inventory" and item_choices:
            self.add_item(ShowItemSelect(target_id, item_choices))
        self.add_item(ShareButton(row=4))


class CharacterShowDetailView(discord.ui.View):
    """Read-only spell/item detail reached from a /character show dropdown,
    with a Back button that returns to the originating tab."""

    def __init__(self, target_id: str, tab: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.target_id = target_id
        self.tab = tab
        self.add_item(ShareButton(row=4))

    @discord.ui.button(label="← Back", style=discord.ButtonStyle.primary, row=1)
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_show_payload(self.target_id, self.tab)
        if payload is None:
            await interaction.response.edit_message(
                content="That character no longer exists.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(content=None, embed=embed, view=view)


class DeleteConfirmView(_OwnerView):
    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        with session_scope() as session:
            characters.delete_character(session, self.user_id)
        await interaction.response.edit_message(
            content="Your character has been deleted.", embed=None, view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_sheet_payload(self.user_id, "roleplaying")
        if payload is None:
            await interaction.response.edit_message(
                content="Deletion cancelled.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(content=None, embed=embed, view=view)


# ---------- Cog ----------


class PlayerCharacters(commands.Cog):
    """Per-player character stats and inventory."""

    character = app_commands.Group(
        name="character", description="Manage your Shadow Dark character"
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @character.command(
        name="sheet",
        description="Open your character sheet (create one if you don't have it)",
    )
    async def sheet(self, interaction: discord.Interaction) -> None:
        user_id = str(interaction.user.id)
        payload = _build_sheet_payload(user_id)
        if payload is None:
            embed = discord.Embed(
                title="No character yet",
                description=(
                    "You don't have a Shadow Dark character yet. "
                    "Press **Create character** to make one."
                ),
                color=CHARACTER_COLOR,
            )
            await interaction.response.send_message(
                embed=embed, view=NoCharacterView(user_id), ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @character.command(
        name="carry", description="Add an item you're carrying (catalog or freeform)"
    )
    @app_commands.describe(
        item="Item name — matching a catalog item links it; anything else is freeform",
        quantity="How many (defaults to 1)",
        gear_slots="Slots per item for a new freeform item (defaults to 1; "
        "ignored for catalog items)",
    )
    async def carry(
        self,
        interaction: discord.Interaction,
        item: str,
        quantity: int = 1,
        gear_slots: float | None = None,
    ) -> None:
        await _do_carry(
            interaction,
            user_id=str(interaction.user.id),
            item_name=item,
            quantity=quantity,
            freeform_slots=gear_slots if gear_slots is not None else 1.0,
        )

    @carry.autocomplete("item")
    async def _ac_carry_item(self, interaction, current):
        with session_scope() as session:
            names = list(
                session.scalars(
                    select(Item.name)
                    .where(Item.name.ilike(f"%{current}%"))
                    .order_by(Item.name)
                    .limit(25)
                ).all()
            )
        return [app_commands.Choice(name=n, value=n) for n in names]

    @character.command(
        name="show", description="View another player's character (read-only)"
    )
    @app_commands.describe(member="Whose character to view")
    async def show(
        self, interaction: discord.Interaction, member: discord.Member
    ) -> None:
        payload = _build_show_payload(str(member.id), "combat")
        if payload is None:
            await interaction.response.send_message(
                f"**{member.display_name}** doesn't have a character yet.",
                ephemeral=True,
            )
            return
        embed, view = payload
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PlayerCharacters(bot))
