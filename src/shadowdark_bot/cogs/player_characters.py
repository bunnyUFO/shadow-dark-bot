"""`/character` — per-player Shadow Dark character stats + inventory.

One character per Discord user. The owner edits their own sheet through an
ephemeral, interactive embed (buttons + modals); `/character show` renders any
player's sheet read-only. Inventory is hybrid: a carried stack either links to
the shared `/items` catalog (reusing its gear-slot data) or is a freeform typed
item. Carry capacity is the Shadow Dark limit, max(10, STR).
"""

import logging
from datetime import UTC, datetime

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from shadowdark_bot.currency import format_cp, parse_value_string
from shadowdark_bot.db import session_scope
from shadowdark_bot.embeds import (
    CHARACTER_COLOR,
    build_character_inventory_embed,
    build_character_sheet_embed,
    build_character_spells_embed,
    build_spell_embed,
    fmt_slots,
)
from shadowdark_bot.models import (
    CharacterItem,
    CharacterSpell,
    Item,
    PlayerCharacter,
    Spell,
)
from shadowdark_bot.rules import (
    ABILITIES,
    SPELL_ABILITIES,
    carry_capacity,
    stack_slots,
)
from shadowdark_bot.sharing import ShareableView, ShareButton

log = logging.getLogger("shadowdark_bot.characters")

VIEW_TIMEOUT_SECONDS = 300


# ---------- Load / parse helpers ----------


def _load_character(session: Session, user_id: str) -> PlayerCharacter | None:
    """Load a character with its items and spells (and their reference rows).

    Uses selectinload for the two collections so they don't cross-join.
    """
    return session.scalars(
        select(PlayerCharacter)
        .options(
            selectinload(PlayerCharacter.items).joinedload(CharacterItem.item),
            selectinload(PlayerCharacter.spells).joinedload(CharacterSpell.spell),
        )
        .where(PlayerCharacter.user_id == user_id)
    ).first()


def _sorted_items(char: PlayerCharacter) -> list[CharacterItem]:
    return sorted(char.items, key=lambda c: c.display_name.lower())


def _sorted_spells(char: PlayerCharacter) -> list[CharacterSpell]:
    return sorted(
        char.spells, key=lambda s: ((s.display_tier or 0), s.display_name.lower())
    )


def _touch(char: PlayerCharacter) -> None:
    char.updated_at = datetime.now(UTC)


# The spellcasting stat picks the spell list a character draws from:
# Wizards cast with INT, Priests with WIS. (No alignment gating.)
_SPELL_CLASS_BY_ABILITY = {"int": "wizard", "wis": "priest"}
_ALIGNMENT_NAMES = {"L": "Lawful", "N": "Neutral", "C": "Chaotic"}


def _spell_class_for(char: PlayerCharacter) -> str | None:
    """The spell class a character can learn from, or None if they aren't a
    caster (no spellcasting stat set)."""
    return _SPELL_CLASS_BY_ABILITY.get(char.spell_ability or "")


def _addable_spells(
    session: Session, char: PlayerCharacter, cls: str, tier: int | None = None
) -> list[Spell]:
    """Reference spells of the character's class (and tier, if given) not already
    known. Any tier is learnable — scrolls and the like can grant higher-tier
    spells — so tiers are never gated by character level."""
    known_ids = {s.spell_id for s in char.spells if s.spell_id is not None}
    stmt = select(Spell).where(Spell.classes.contains(cls))
    if tier is not None:
        stmt = stmt.where(Spell.tier == tier)
    stmt = stmt.order_by(Spell.tier, Spell.name)
    return [sp for sp in session.scalars(stmt).all() if sp.id not in known_ids]


def _spell_choice_label(spell: Spell) -> str:
    label = spell.name
    if spell.alignment:
        label += f" ({_ALIGNMENT_NAMES.get(spell.alignment, spell.alignment)})"
    return label


def _parse_scores(raw: str) -> list[int]:
    """Parse 'STR DEX CON INT WIS CHA' into six ints (space/comma separated)."""
    parts = raw.replace(",", " ").split()
    if len(parts) != 6:
        raise ValueError("Enter exactly six scores, e.g. `14 12 13 10 8 15`.")
    scores: list[int] = []
    for p in parts:
        try:
            n = int(p)
        except ValueError as err:
            raise ValueError(f'"{p}" is not a whole number.') from err
        if not 1 <= n <= 30:
            raise ValueError(f"Score {n} is out of range (1–30).")
        scores.append(n)
    return scores


def _parse_optional_int(raw: str, *, minimum: int | None = None) -> int | None:
    raw = raw.strip()
    if not raw:
        return None
    n = int(raw)
    if minimum is not None and n < minimum:
        raise ValueError(f"Must be ≥ {minimum}.")
    return n


# ---------- Payload builders ----------


def _build_sheet_payload(user_id: str) -> tuple[discord.Embed, "CharacterSheetView"] | None:
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            return None
        embed = build_character_sheet_embed(
            char, _sorted_items(char), _sorted_spells(char)
        )
    return embed, CharacterSheetView(user_id)


def _build_inventory_payload(
    user_id: str,
) -> tuple[discord.Embed, "CharacterInventoryView"] | None:
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            return None
        items = _sorted_items(char)
        embed = build_character_inventory_embed(char, items)
        choices = [(ci.id, f"{ci.quantity}× {ci.display_name}") for ci in items]
    return embed, CharacterInventoryView(user_id, choices)


def _build_item_detail_payload(
    user_id: str, ci_id: int
) -> tuple[discord.Embed, "CharacterItemDetailView"] | None:
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            return None
        ci = next((c for c in char.items if c.id == ci_id), None)
        if ci is None:
            return None
        embed = discord.Embed(title=ci.display_name, color=CHARACTER_COLOR)
        per = fmt_slots(ci.effective_gear_slots)
        cost = f"{fmt_slots(ci.slot_cost)} ({per} each"
        if ci.effective_bundle_size > 1:
            cost += f", {ci.effective_bundle_size}/slot"
        cost += ")"
        lines = [
            f"**Quantity:** {ci.quantity}",
            f"**Slot cost:** {cost}",
            f"**Source:** {'Catalog' if ci.is_catalog else 'Freeform'}",
        ]
        if ci.notes:
            lines.append(f"_{ci.notes}_")
        embed.description = "\n".join(lines)
        qty = ci.quantity
        name = ci.display_name
    return embed, CharacterItemDetailView(user_id, ci_id, qty, name)


def _build_show_payload(
    target_id: str, mode: str
) -> tuple[discord.Embed, "CharacterShowView"] | None:
    with session_scope() as session:
        char = _load_character(session, target_id)
        if char is None:
            return None
        items = _sorted_items(char)
        if mode == "inventory":
            embed = build_character_inventory_embed(char, items)
        else:
            embed = build_character_sheet_embed(char, items, _sorted_spells(char))
    return embed, CharacterShowView(target_id, mode)


# ---------- Spell management (bespoke, class-limited) ----------


def _build_spells_payload(
    user_id: str,
) -> tuple[discord.Embed, "CharacterSpellsView"] | None:
    """Manage-spells landing: your known spells (with a Forget path) plus a
    Learn button."""
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            return None
        spells = _sorted_spells(char)
        embed = build_character_spells_embed(char, spells)
        is_caster = _spell_class_for(char) is not None
        choices = [
            (cs.id, cs.display_name + (f" (T{cs.display_tier})" if cs.display_tier else ""))
            for cs in spells
        ]
    return embed, CharacterSpellsView(user_id, choices, is_caster)


def _build_char_spell_detail_payload(
    user_id: str, cs_id: int
) -> tuple[discord.Embed, "CharacterSpellDetailView"] | None:
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            return None
        cs = next((s for s in char.spells if s.id == cs_id), None)
        if cs is None:
            return None
        if cs.is_reference and cs.spell is not None:
            embed = build_spell_embed(cs.spell)
        else:
            embed = discord.Embed(title=cs.display_name, color=CHARACTER_COLOR)
            tier = cs.display_tier
            embed.description = (f"**Tier {tier}**\n" if tier else "") + "_(no reference text)_"
        name = cs.display_name
    return embed, CharacterSpellDetailView(user_id, cs_id, name)


def _build_learn_tier_payload(
    user_id: str,
) -> tuple[discord.Embed, "LearnTierView"] | None:
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            return None
        cls = _spell_class_for(char)
        if cls is None:
            return None
        addable = _addable_spells(session, char, cls)
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
        char = _load_character(session, user_id)
        if char is None:
            return None
        cls = _spell_class_for(char)
        if cls is None:
            return None
        choices = [
            (sp.name, _spell_choice_label(sp))
            for sp in _addable_spells(session, char, cls, tier)
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


async def _refresh_sheet(interaction: discord.Interaction, user_id: str) -> None:
    payload = _build_sheet_payload(user_id)
    if payload is None:
        await interaction.response.edit_message(
            content="Character not found.", embed=None, view=None
        )
        return
    embed, view = payload
    await interaction.response.edit_message(content=None, embed=embed, view=view)


# ---------- Write logic (carry / add-more / take / remove) ----------


async def _do_carry(
    interaction: discord.Interaction,
    *,
    user_id: str,
    item_name: str,
    quantity: int,
    freeform_slots: float = 1.0,
    edit_view: bool = False,
) -> None:
    clean_item = item_name.strip()
    failure = f"**Failed to carry {quantity}× {clean_item}.**"
    if not clean_item:
        await interaction.response.send_message(
            f"{failure}\nItem name cannot be empty.", ephemeral=True
        )
        return
    if quantity < 1:
        await interaction.response.send_message(
            f"{failure}\nQuantity must be ≥ 1.", ephemeral=True
        )
        return
    if freeform_slots < 0:
        await interaction.response.send_message(
            f"{failure}\nGear slots each must be ≥ 0.", ephemeral=True
        )
        return

    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            await interaction.response.send_message(
                f"{failure}\nYou don't have a character yet — run `/character sheet`.",
                ephemeral=True,
            )
            return

        cat_item = session.scalar(select(Item).where(Item.name == clean_item))

        stack: CharacterItem | None = None
        if cat_item is not None:
            stack = next(
                (c for c in char.items if c.item_id == cat_item.id), None
            )
        else:
            stack = next(
                (
                    c
                    for c in char.items
                    if c.item_id is None
                    and (c.name or "").lower() == clean_item.lower()
                ),
                None,
            )

        if cat_item is not None:
            gear_slots, bundle = cat_item.gear_slots, cat_item.bundle_size
        elif stack is not None:
            gear_slots, bundle = stack.effective_gear_slots, stack.effective_bundle_size
        else:
            gear_slots, bundle = freeform_slots, 1

        current_qty = stack.quantity if stack else 0
        new_qty = current_qty + quantity
        delta = stack_slots(new_qty, gear_slots, bundle) - stack_slots(
            current_qty, gear_slots, bundle
        )
        used = sum(c.slot_cost for c in char.items)
        free = carry_capacity(char.str_score)
        new_used = used + delta
        if new_used > free:
            await interaction.response.send_message(
                f"{failure}\nYou're carrying {fmt_slots(used)}/{free} slots; "
                f"this would need {fmt_slots(delta)} more ({fmt_slots(new_used)} total). "
                "Drop something or raise STR.",
                ephemeral=True,
            )
            return

        if stack is None:
            if cat_item is not None:
                stack = CharacterItem(
                    character_id=char.id, item_id=cat_item.id, quantity=quantity
                )
            else:
                stack = CharacterItem(
                    character_id=char.id,
                    name=clean_item,
                    quantity=quantity,
                    slots_each=freeform_slots,
                    bundle_size=1,
                )
            session.add(stack)
        else:
            stack.quantity = new_qty
        _touch(char)
        session.flush()
        display = stack.display_name

    confirmation = (
        f"Added {quantity}× **{display}** to your inventory. "
        f"Carrying {fmt_slots(new_used)}/{free} slots."
    )
    await _respond(interaction, confirmation, user_id, edit_view)


async def _do_add_more(
    interaction: discord.Interaction,
    *,
    user_id: str,
    ci_id: int,
    quantity: int,
) -> None:
    failure = "**Failed to add more.**"
    if quantity < 1:
        await interaction.response.send_message(
            f"{failure}\nQuantity must be ≥ 1.", ephemeral=True
        )
        return
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            await interaction.response.send_message(
                f"{failure}\nCharacter not found.", ephemeral=True
            )
            return
        ci = next((c for c in char.items if c.id == ci_id), None)
        if ci is None:
            await interaction.response.send_message(
                f"{failure}\nThat item is no longer in your inventory.", ephemeral=True
            )
            return
        gear_slots, bundle = ci.effective_gear_slots, ci.effective_bundle_size
        delta = stack_slots(ci.quantity + quantity, gear_slots, bundle) - stack_slots(
            ci.quantity, gear_slots, bundle
        )
        used = sum(c.slot_cost for c in char.items)
        free = carry_capacity(char.str_score)
        new_used = used + delta
        if new_used > free:
            await interaction.response.send_message(
                f"{failure}\nYou're carrying {fmt_slots(used)}/{free} slots; "
                f"this would need {fmt_slots(delta)} more.",
                ephemeral=True,
            )
            return
        ci.quantity += quantity
        _touch(char)
        session.flush()
        display = ci.display_name

    confirmation = (
        f"Added {quantity}× **{display}**. Carrying {fmt_slots(new_used)}/{free} slots."
    )
    await _respond(interaction, confirmation, user_id, edit_view=True)


async def _do_take(
    interaction: discord.Interaction,
    *,
    user_id: str,
    ci_id: int,
    quantity: int,
) -> None:
    failure = "**Failed to take.**"
    if quantity < 1:
        await interaction.response.send_message(
            f"{failure}\nQuantity must be ≥ 1.", ephemeral=True
        )
        return
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            await interaction.response.send_message(
                f"{failure}\nCharacter not found.", ephemeral=True
            )
            return
        ci = next((c for c in char.items if c.id == ci_id), None)
        if ci is None:
            await interaction.response.send_message(
                f"{failure}\nThat item is no longer in your inventory.", ephemeral=True
            )
            return
        if quantity > ci.quantity:
            await interaction.response.send_message(
                f"{failure}\nYou only have {ci.quantity}× **{ci.display_name}**.",
                ephemeral=True,
            )
            return
        display = ci.display_name
        ci.quantity -= quantity
        emptied = ci.quantity == 0
        if emptied:
            session.delete(ci)
        _touch(char)

    confirmation = (
        f"Dropped the last {quantity}× **{display}**."
        if emptied
        else f"Dropped {quantity}× **{display}**."
    )
    await _respond(interaction, confirmation, user_id, edit_view=True)


async def _do_remove(
    interaction: discord.Interaction, *, user_id: str, ci_id: int
) -> None:
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            await interaction.response.send_message(
                "**Failed to remove.**\nCharacter not found.", ephemeral=True
            )
            return
        ci = next((c for c in char.items if c.id == ci_id), None)
        if ci is None:
            await interaction.response.send_message(
                "**Failed to remove.**\nThat item is already gone.", ephemeral=True
            )
            return
        display = ci.display_name
        session.delete(ci)
        _touch(char)

    await _respond(
        interaction, f"Removed **{display}** from your inventory.", user_id, edit_view=True
    )


async def _respond(
    interaction: discord.Interaction, confirmation: str, user_id: str, edit_view: bool
) -> None:
    """For interactive (edit_view) actions, refresh the inventory view in place
    and follow up with an ephemeral confirmation; otherwise reply directly."""
    if edit_view:
        payload = _build_inventory_payload(user_id)
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
    """Learn a reference spell of the character's class. Any tier is allowed;
    only the class is checked (no alignment gating)."""
    failure = f"**Failed to learn {spell_name}.**"
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            await interaction.response.send_message(
                f"{failure}\nCharacter not found.", ephemeral=True
            )
            return
        cls = _spell_class_for(char)
        if cls is None:
            await interaction.response.send_message(
                f"{failure}\nYour character isn't a spellcaster — set a "
                "spellcasting stat (INT or WIS) via **Edit Talents & Casting**.",
                ephemeral=True,
            )
            return
        sp = session.scalar(select(Spell).where(Spell.name == spell_name))
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
        _touch(char)
        session.flush()

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
    with session_scope() as session:
        char = _load_character(session, user_id)
        if char is None:
            await interaction.response.send_message(
                "**Failed to forget.**\nCharacter not found.", ephemeral=True
            )
            return
        cs = next((s for s in char.spells if s.id == cs_id), None)
        if cs is None:
            await interaction.response.send_message(
                "**Failed to forget.**\nYou don't know that spell.", ephemeral=True
            )
            return
        display = cs.display_name
        session.delete(cs)
        _touch(char)

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


class EditDetailsModal(discord.ui.Modal):
    """Upsert: creates the character if none exists, else edits identity fields."""

    def __init__(
        self,
        user_id: str,
        *,
        name: str = "",
        char_class: str = "",
        level: str = "1",
        max_hp: str = "",
        ac: str = "",
    ) -> None:
        super().__init__(title="Character details")
        self.user_id = user_id
        self._name = discord.ui.TextInput(
            label="Name", required=True, default=name, max_length=100
        )
        self._class = discord.ui.TextInput(
            label="Class", required=False, default=char_class, max_length=50
        )
        self._level = discord.ui.TextInput(
            label="Level", required=True, default=level, max_length=3
        )
        self._hp = discord.ui.TextInput(
            label="Max HP (blank = unset)", required=False, default=max_hp, max_length=4
        )
        self._ac = discord.ui.TextInput(
            label="Armor Class (blank = unset)", required=False, default=ac, max_length=3
        )
        for field in (self._name, self._class, self._level, self._hp, self._ac):
            self.add_item(field)

    @classmethod
    def from_char(cls, char: PlayerCharacter) -> "EditDetailsModal":
        return cls(
            char.user_id,
            name=char.name,
            char_class=char.char_class or "",
            level=str(char.level),
            max_hp=str(char.max_hp) if char.max_hp is not None else "",
            ac=str(char.armor_class) if char.armor_class is not None else "",
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = str(self._name.value).strip()
        if not name:
            await interaction.response.send_message(
                "**Failed to save.**\nName cannot be empty.", ephemeral=True
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
            max_hp = _parse_optional_int(str(self._hp.value), minimum=0)
            armor_class = _parse_optional_int(str(self._ac.value))
        except ValueError:
            await interaction.response.send_message(
                "**Failed to save.**\nMax HP and AC must be whole numbers.",
                ephemeral=True,
            )
            return

        with session_scope() as session:
            char = session.scalar(
                select(PlayerCharacter).where(PlayerCharacter.user_id == self.user_id)
            )
            if char is None:
                char = PlayerCharacter(user_id=self.user_id, name=name)
                session.add(char)
            char.name = name
            char.char_class = str(self._class.value).strip() or None
            char.level = level
            char.max_hp = max_hp
            char.armor_class = armor_class
            _touch(char)
            session.flush()

        await _refresh_sheet(interaction, self.user_id)


class EditAbilitiesModal(discord.ui.Modal):
    def __init__(self, user_id: str, *, scores: str = "", gold: str = "") -> None:
        super().__init__(title="Abilities & gold")
        self.user_id = user_id
        self._scores = discord.ui.TextInput(
            label="Scores: STR DEX CON INT WIS CHA",
            required=True,
            default=scores,
            max_length=40,
        )
        self._gold = discord.ui.TextInput(
            label='Gold (e.g. "10gp 5sp"; blank = 0)',
            required=False,
            default=gold,
            max_length=50,
        )
        self.add_item(self._scores)
        self.add_item(self._gold)

    @classmethod
    def from_char(cls, char: PlayerCharacter) -> "EditAbilitiesModal":
        scores = " ".join(str(getattr(char, f"{k}_score")) for k, _ in ABILITIES)
        return cls(char.user_id, scores=scores, gold=format_cp(char.gold_cp) or "0cp")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            scores = _parse_scores(str(self._scores.value))
        except ValueError as err:
            await interaction.response.send_message(
                f"**Failed to save.**\n{err}", ephemeral=True
            )
            return
        try:
            gold = parse_value_string(str(self._gold.value))
        except ValueError as err:
            await interaction.response.send_message(
                f"**Failed to save.**\n{err}", ephemeral=True
            )
            return

        with session_scope() as session:
            char = session.scalar(
                select(PlayerCharacter).where(PlayerCharacter.user_id == self.user_id)
            )
            if char is None:
                await interaction.response.send_message(
                    "Character not found — run `/character sheet` first.",
                    ephemeral=True,
                )
                return
            for (key, _), value in zip(ABILITIES, scores, strict=True):
                setattr(char, f"{key}_score", value)
            char.gold_cp = gold if gold is not None else 0
            _touch(char)
            session.flush()

        await _refresh_sheet(interaction, self.user_id)


class EditTalentsCastingModal(discord.ui.Modal):
    def __init__(
        self,
        user_id: str,
        *,
        talents: str = "",
        spell_stat: str = "",
        spell_bonus: str = "0",
    ) -> None:
        super().__init__(title="Talents & casting")
        self.user_id = user_id
        self._talents = discord.ui.TextInput(
            label="Talents",
            required=False,
            default=talents,
            style=discord.TextStyle.paragraph,
            max_length=1000,
        )
        self._spell_stat = discord.ui.TextInput(
            label="Spellcasting stat (INT / WIS / none)",
            required=False,
            default=spell_stat,
            max_length=4,
        )
        self._spell_bonus = discord.ui.TextInput(
            label="Spell check bonus (from talents)",
            required=False,
            default=spell_bonus,
            max_length=3,
        )
        self.add_item(self._talents)
        self.add_item(self._spell_stat)
        self.add_item(self._spell_bonus)

    @classmethod
    def from_char(cls, char: PlayerCharacter) -> "EditTalentsCastingModal":
        return cls(
            char.user_id,
            talents=char.talents or "",
            spell_stat=char.spell_ability.upper() if char.spell_ability else "",
            spell_bonus=str(char.spell_check_bonus),
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_stat = str(self._spell_stat.value).strip().lower()
        if raw_stat in ("", "none", "-"):
            spell_ability: str | None = None
        elif raw_stat in SPELL_ABILITIES:
            spell_ability = raw_stat
        else:
            await interaction.response.send_message(
                "**Failed to save.**\nSpellcasting stat must be INT, WIS, or none.",
                ephemeral=True,
            )
            return
        try:
            bonus = _parse_optional_int(str(self._spell_bonus.value)) or 0
        except ValueError:
            await interaction.response.send_message(
                "**Failed to save.**\nSpell check bonus must be a whole number.",
                ephemeral=True,
            )
            return

        with session_scope() as session:
            char = session.scalar(
                select(PlayerCharacter).where(PlayerCharacter.user_id == self.user_id)
            )
            if char is None:
                await interaction.response.send_message(
                    "Character not found — run `/character sheet` first.",
                    ephemeral=True,
                )
                return
            char.talents = str(self._talents.value).strip() or None
            char.spell_ability = spell_ability
            char.spell_check_bonus = bonus
            _touch(char)
            session.flush()

        await _refresh_sheet(interaction, self.user_id)


_ALIGNMENTS = {"lawful": "Lawful", "neutral": "Neutral", "chaotic": "Chaotic"}


class EditIdentityModal(discord.ui.Modal):
    def __init__(
        self,
        user_id: str,
        *,
        ancestry: str = "",
        alignment: str = "",
        languages: str = "",
    ) -> None:
        super().__init__(title="Identity")
        self.user_id = user_id
        self._ancestry = discord.ui.TextInput(
            label="Ancestry", required=False, default=ancestry, max_length=50
        )
        self._alignment = discord.ui.TextInput(
            label="Alignment (Lawful / Neutral / Chaotic)",
            required=False,
            default=alignment,
            max_length=10,
        )
        self._languages = discord.ui.TextInput(
            label="Known languages (comma-separated)",
            required=False,
            default=languages,
            style=discord.TextStyle.paragraph,
            max_length=300,
        )
        self.add_item(self._ancestry)
        self.add_item(self._alignment)
        self.add_item(self._languages)

    @classmethod
    def from_char(cls, char: PlayerCharacter) -> "EditIdentityModal":
        return cls(
            char.user_id,
            ancestry=char.ancestry or "",
            alignment=char.alignment or "",
            languages=char.languages or "",
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw_align = str(self._alignment.value).strip().lower()
        if raw_align in ("", "none", "-"):
            alignment: str | None = None
        elif raw_align in _ALIGNMENTS:
            alignment = _ALIGNMENTS[raw_align]
        else:
            await interaction.response.send_message(
                "**Failed to save.**\nAlignment must be Lawful, Neutral, "
                "Chaotic, or none.",
                ephemeral=True,
            )
            return

        with session_scope() as session:
            char = session.scalar(
                select(PlayerCharacter).where(PlayerCharacter.user_id == self.user_id)
            )
            if char is None:
                await interaction.response.send_message(
                    "Character not found — run `/character sheet` first.",
                    ephemeral=True,
                )
                return
            char.ancestry = str(self._ancestry.value).strip() or None
            char.alignment = alignment
            char.languages = str(self._languages.value).strip() or None
            _touch(char)
            session.flush()

        await _refresh_sheet(interaction, self.user_id)


class AddItemModal(discord.ui.Modal):
    """Add a carried item by name. A name matching the catalog links to it;
    otherwise a freeform stack is created with the given per-item slot cost."""

    def __init__(self, user_id: str) -> None:
        super().__init__(title="Add an item")
        self.user_id = user_id
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
        self.add_item(self._name)
        self.add_item(self._qty)
        self.add_item(self._slots)

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
            item_name=str(self._name.value),
            quantity=quantity,
            freeform_slots=freeform_slots,
            edit_view=True,
        )


class _QuantityModal(discord.ui.Modal):
    """Shared single-quantity modal for the item detail Add-more / Take buttons."""

    def __init__(self, title: str, user_id: str, ci_id: int, *, action: str) -> None:
        super().__init__(title=title[:45])
        self.user_id = user_id
        self.ci_id = ci_id
        self.action = action
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
        if self.action == "add":
            await _do_add_more(
                interaction, user_id=self.user_id, ci_id=self.ci_id, quantity=quantity
            )
        else:
            await _do_take(
                interaction, user_id=self.user_id, ci_id=self.ci_id, quantity=quantity
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
        await interaction.response.send_modal(EditDetailsModal(self.user_id))


class CharacterSheetView(_OwnerView):
    def __init__(self, user_id: str) -> None:
        super().__init__(user_id)
        self.add_item(ShareButton(row=4))

    async def _open(self, interaction: discord.Interaction, factory) -> None:
        with session_scope() as session:
            char = session.scalar(
                select(PlayerCharacter).where(PlayerCharacter.user_id == self.user_id)
            )
            if char is None:
                await interaction.response.send_message(
                    "Character not found.", ephemeral=True
                )
                return
            modal = factory(char)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Edit Details", style=discord.ButtonStyle.primary, row=0)
    async def edit_details(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, EditDetailsModal.from_char)

    @discord.ui.button(
        label="Edit Abilities & Gold", style=discord.ButtonStyle.primary, row=0
    )
    async def edit_abilities(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, EditAbilitiesModal.from_char)

    @discord.ui.button(
        label="Edit Talents & Casting", style=discord.ButtonStyle.primary, row=0
    )
    async def edit_talents(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, EditTalentsCastingModal.from_char)

    @discord.ui.button(
        label="Edit Identity", style=discord.ButtonStyle.primary, row=0
    )
    async def edit_identity(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._open(interaction, EditIdentityModal.from_char)

    @discord.ui.button(
        label="Manage Inventory", style=discord.ButtonStyle.secondary, row=1
    )
    async def manage_inventory(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_inventory_payload(self.user_id)
        if payload is None:
            await interaction.response.send_message(
                "Character not found.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(
        label="Manage Spells", style=discord.ButtonStyle.secondary, row=1
    )
    async def manage_spells(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_spells_payload(self.user_id)
        if payload is None:
            await interaction.response.send_message(
                "Character not found.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(
        label="Delete character", style=discord.ButtonStyle.danger, row=2
    )
    async def delete(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        with session_scope() as session:
            char = _load_character(session, self.user_id)
            if char is None:
                await interaction.response.send_message(
                    "Character not found.", ephemeral=True
                )
                return
            name = char.name
            count = len(char.items)
        embed = discord.Embed(
            title="Delete character?",
            description=(
                f"Are you sure you want to delete **{name}**? "
                f"This also drops its {count} carried stack(s) and all known "
                "spells, and can't be undone."
            ),
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(
            embed=embed, view=DeleteConfirmView(self.user_id)
        )


class CharacterItemSelect(discord.ui.Select):
    def __init__(self, user_id: str, choices: list[tuple[int, str]]) -> None:
        options = [
            discord.SelectOption(label=label[:100], value=str(cid))
            for cid, label in choices[:25]
        ]
        super().__init__(
            placeholder="Inspect a carried item…",
            options=options,
            min_values=1,
            max_values=1,
            row=0,
        )
        self.user_id = user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        payload = _build_item_detail_payload(self.user_id, int(self.values[0]))
        if payload is None:
            await interaction.response.send_message(
                "That item is no longer in your inventory.", ephemeral=True
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)


class CharacterInventoryView(_OwnerView):
    def __init__(self, user_id: str, choices: list[tuple[int, str]]) -> None:
        super().__init__(user_id)
        if choices:
            self.add_item(CharacterItemSelect(user_id, choices))
        self.add_item(ShareButton(row=4))

    @discord.ui.button(label="Add item", style=discord.ButtonStyle.success, row=1)
    async def add_item_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await interaction.response.send_modal(AddItemModal(self.user_id))

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


class _AddMoreButton(discord.ui.Button):
    def __init__(self, user_id: str, ci_id: int, item_name: str) -> None:
        super().__init__(label="+ Add more", style=discord.ButtonStyle.success, row=0)
        self.user_id = user_id
        self.ci_id = ci_id
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            _QuantityModal(
                f"Add more {self.item_name}", self.user_id, self.ci_id, action="add"
            )
        )


class _TakeButton(discord.ui.Button):
    def __init__(self, user_id: str, ci_id: int, item_name: str, qty: int) -> None:
        super().__init__(
            label="− Take", style=discord.ButtonStyle.danger, row=0, disabled=qty <= 0
        )
        self.user_id = user_id
        self.ci_id = ci_id
        self.item_name = item_name

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(
            _QuantityModal(
                f"Take {self.item_name}", self.user_id, self.ci_id, action="take"
            )
        )


class CharacterItemDetailView(_OwnerView):
    def __init__(self, user_id: str, ci_id: int, qty: int, item_name: str) -> None:
        super().__init__(user_id)
        self.ci_id = ci_id
        self.add_item(_AddMoreButton(user_id, ci_id, item_name))
        self.add_item(_TakeButton(user_id, ci_id, item_name, qty))
        self.add_item(ShareButton(row=4))

    @discord.ui.button(label="Remove", style=discord.ButtonStyle.danger, row=0)
    async def remove(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _do_remove(interaction, user_id=self.user_id, ci_id=self.ci_id)

    @discord.ui.button(
        label="← Back to inventory", style=discord.ButtonStyle.primary, row=1
    )
    async def back(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_inventory_payload(self.user_id)
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
                "(INT or WIS) via **Edit Talents & Casting** first.",
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


class CharacterShowView(discord.ui.View):
    """Read-only view of another player's sheet — toggle Stats/Inventory + share."""

    def __init__(self, target_id: str, mode: str) -> None:
        super().__init__(timeout=VIEW_TIMEOUT_SECONDS)
        self.target_id = target_id
        self.mode = mode

        stats = discord.ui.Button(
            label="Stats",
            style=discord.ButtonStyle.secondary
            if mode == "stats"
            else discord.ButtonStyle.primary,
            disabled=mode == "stats",
            row=0,
        )
        inv = discord.ui.Button(
            label="Inventory",
            style=discord.ButtonStyle.secondary
            if mode == "inventory"
            else discord.ButtonStyle.primary,
            disabled=mode == "inventory",
            row=0,
        )
        stats.callback = self._show_stats  # type: ignore[method-assign]
        inv.callback = self._show_inventory  # type: ignore[method-assign]
        self.add_item(stats)
        self.add_item(inv)
        self.add_item(ShareButton(row=4))

    async def _swap(self, interaction: discord.Interaction, mode: str) -> None:
        payload = _build_show_payload(self.target_id, mode)
        if payload is None:
            await interaction.response.edit_message(
                content="That character no longer exists.", embed=None, view=None
            )
            return
        embed, view = payload
        await interaction.response.edit_message(embed=embed, view=view)

    async def _show_stats(self, interaction: discord.Interaction) -> None:
        await self._swap(interaction, "stats")

    async def _show_inventory(self, interaction: discord.Interaction) -> None:
        await self._swap(interaction, "inventory")


class DeleteConfirmView(_OwnerView):
    @discord.ui.button(label="Delete", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        with session_scope() as session:
            char = session.scalar(
                select(PlayerCharacter).where(PlayerCharacter.user_id == self.user_id)
            )
            if char is not None:
                session.delete(char)
        await interaction.response.edit_message(
            content="Your character has been deleted.", embed=None, view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        payload = _build_sheet_payload(self.user_id)
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
        payload = _build_show_payload(str(member.id), "stats")
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
