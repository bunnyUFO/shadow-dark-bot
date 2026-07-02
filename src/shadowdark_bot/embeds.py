from datetime import UTC, datetime

import discord

from shadowdark_bot.currency import format_cp
from shadowdark_bot.models import (
    ITEM_TYPE_ARMOR,
    ITEM_TYPE_COMMON,
    ITEM_TYPE_CRAFTED,
    ITEM_TYPE_LOOT,
    ITEM_TYPE_MAGICAL,
    ITEM_TYPE_POTION,
    ITEM_TYPE_SCROLL,
    ITEM_TYPE_WEAPON,
    Borrow,
    CharacterItem,
    CharacterSpell,
    InventoryEntry,
    Item,
    Location,
    PlayerCharacter,
    Spell,
    TreasuryEntry,
)
from shadowdark_bot.rules import (
    ABILITIES,
    ability_modifier,
    carry_capacity,
    format_modifier,
    spellcasting_modifier,
)

_ITEM_TYPE_DISPLAY: dict[str, tuple[str, discord.Color]] = {
    ITEM_TYPE_COMMON: ("Common", discord.Color.blue()),
    ITEM_TYPE_MAGICAL: ("Magical", discord.Color.purple()),
    ITEM_TYPE_CRAFTED: ("Crafted", discord.Color.green()),
    ITEM_TYPE_SCROLL: ("Scroll", discord.Color.orange()),
    ITEM_TYPE_POTION: ("Potion", discord.Color.teal()),
    ITEM_TYPE_WEAPON: ("Weapon", discord.Color.red()),
    ITEM_TYPE_ARMOR: ("Armor", discord.Color.light_grey()),
    ITEM_TYPE_LOOT: ("Loot", discord.Color.yellow()),
}


_RENDERED_FIELD_PREFIXES = (
    "**Gear slots:**",
    "**Type:**",
    "**Value:**",
    "**Status:**",
)


def _clean_description(description: str) -> str:
    kept = [
        line
        for line in description.split("\n")
        if not line.strip().startswith(_RENDERED_FIELD_PREFIXES)
    ]
    return "\n".join(kept).strip("\n")


def build_item_embed(item: Item) -> discord.Embed:
    label, color = _ITEM_TYPE_DISPLAY.get(
        item.item_type, ("Common", discord.Color.blue())
    )
    embed = discord.Embed(title=item.name, color=color)

    lines: list[str] = []
    if item.description:
        description = _clean_description(item.description)
        if description:
            lines.append(description)
            lines.append("")
    if item.bundle_size > 1:
        lines.append(
            f"**Gear slots:** {fmt_slots(item.gear_slots)} per {item.bundle_size}"
        )
    else:
        lines.append(f"**Gear slots:** {fmt_slots(item.gear_slots)}")
    lines.append(f"**Type:** {label}")
    value = format_cp(item.value_cp)
    if value is not None:
        lines.append(f"**Value:** {value}")

    embed.description = "\n".join(lines)
    return embed


def build_location_summary_embed(
    summaries: list[tuple[Location, float, int]],
) -> discord.Embed:
    """summaries: list of (location, used_slots, stack_count) tuples."""
    embed = discord.Embed(
        title=f"Inventory Locations ({len(summaries)})",
        color=discord.Color.dark_gray(),
    )
    lines: list[str] = []
    for loc, used, count in summaries:
        cap = f"{fmt_slots(used)}/{fmt_slots(loc.max_gear_slots)} slots"
        stack_word = "stack" if count == 1 else "stacks"
        lines.append(f"**{loc.name}** — {count} {stack_word} ({cap})")
    embed.description = "\n".join(lines)
    return embed


def build_location_detail_embed(
    location: Location,
    stacks: list[InventoryEntry],
    used_slots: float,
) -> discord.Embed:
    embed = discord.Embed(
        title=location.name,
        description=location.description or None,
        color=discord.Color.dark_gray(),
    )

    capacity_line = (
        f"**Capacity:** {fmt_slots(used_slots)}/{fmt_slots(location.max_gear_slots)} slots used"
    )

    if not stacks:
        embed.add_field(name="Contents", value=f"{capacity_line}\n\n_(empty)_", inline=False)
        return embed

    lines = [capacity_line, ""]
    visible = stacks[:25]
    for stack in visible:
        lines.append(f"• {stack.quantity}× {stack.item.name}")
    if len(stacks) > 25:
        lines.append(f"…and {len(stacks) - 25} more")

    embed.add_field(name="Contents", value="\n".join(lines), inline=False)
    return embed


# ---------- Treasury ----------

TREASURY_COLOR = discord.Color.purple()


def build_treasury_list_embed(
    available: list[TreasuryEntry],
    borrowed: list[tuple[TreasuryEntry, Borrow | None]],
    status_filter: str | None,
) -> discord.Embed:
    total_count = len(available) + len(borrowed)
    total_slots = sum(e.item.gear_slots for e in available)
    total_slots += sum(e.item.gear_slots for e, _ in borrowed)

    if status_filter == "available":
        title = f"Treasury — Available ({len(available)})"
    elif status_filter == "borrowed":
        title = f"Treasury — Borrowed ({len(borrowed)})"
    else:
        plural = "instance" if total_count == 1 else "instances"
        title = f"Treasury ({total_count} {plural}, {fmt_slots(total_slots)} gear slots)"

    embed = discord.Embed(title=title, color=TREASURY_COLOR)

    sections: list[str] = []
    if status_filter != "borrowed" and available:
        lines = [f"**Available ({len(available)})**"]
        for entry in available[:25]:
            lines.append(_format_treasury_line(entry, None))
        if len(available) > 25:
            lines.append(f"…and {len(available) - 25} more")
        sections.append("\n".join(lines))

    if status_filter != "available" and borrowed:
        lines = [f"**Borrowed ({len(borrowed)})**"]
        for entry, borrow_row in borrowed[:25]:
            lines.append(_format_treasury_line(entry, borrow_row))
        if len(borrowed) > 25:
            lines.append(f"…and {len(borrowed) - 25} more")
        sections.append("\n".join(lines))

    embed.description = "\n\n".join(sections) if sections else "_(empty)_"
    return embed


def build_treasury_instance_embed(
    entry: TreasuryEntry,
    actor_mention: str,
    action: str,
) -> discord.Embed:
    """Confirmation embed for /treasury add and /treasury remove."""
    title = f"#{entry.id}  {entry.item.name}"
    if entry.tag:
        title += f" ({entry.tag})"

    embed = discord.Embed(title=title, color=TREASURY_COLOR)
    if action == "added":
        embed.description = f"Added to the treasury by {actor_mention}."
    elif action == "removed":
        embed.description = f"Removed from the treasury by {actor_mention}."
    return embed


def build_treasury_info_embed(
    entry: TreasuryEntry,
    open_borrow: Borrow | None = None,
) -> discord.Embed:
    """Static info embed for one treasury entry — used by the interactive
    list's drill-down. Mirrors build_item_embed's fields plus current status."""
    title = f"#{entry.id}  {entry.item.name}"
    if entry.tag:
        title += f" ({entry.tag})"
    embed = discord.Embed(title=title, color=TREASURY_COLOR)

    lines: list[str] = []
    if entry.item.description:
        description = _clean_description(entry.item.description)
        if description:
            lines.append(description)
            lines.append("")
    if entry.item.bundle_size > 1:
        lines.append(
            f"**Gear slots:** {fmt_slots(entry.item.gear_slots)} per {entry.item.bundle_size}"
        )
    else:
        lines.append(f"**Gear slots:** {fmt_slots(entry.item.gear_slots)}")
    value = format_cp(entry.item.value_cp)
    if value is not None:
        lines.append(f"**Value:** {value}")

    if open_borrow is not None:
        timesince = format_timesince(open_borrow.borrowed_at)
        lines.append(
            f"**Status:** Borrowed by <@{open_borrow.borrower_id}>, {timesince}"
        )
        if open_borrow.notes:
            lines.append(f"_Notes: {open_borrow.notes}_")
    else:
        lines.append("**Status:** Available")

    embed.description = "\n".join(lines)
    return embed


def _format_treasury_line(entry: TreasuryEntry, borrow_row: Borrow | None) -> str:
    label = f"#{entry.id}  {entry.item.name}"
    if entry.tag:
        label += f" ({entry.tag})"
    if borrow_row is not None:
        label += (
            f" — <@{borrow_row.borrower_id}>, "
            f"{format_timesince(borrow_row.borrowed_at)}"
        )
        if borrow_row.notes:
            label += f"\n   _{borrow_row.notes}_"
    return f"• {label}"


# ---------- Player characters ----------

CHARACTER_COLOR = discord.Color.dark_teal()

_ALIGNMENT_NAMES = {"L": "Lawful", "N": "Neutral", "C": "Chaotic"}


def _ability_table(char: PlayerCharacter) -> str:
    """A two-column code block of scores and modifiers (STR/DEX/CON | INT/WIS/CHA)."""
    left = ABILITIES[:3]
    right = ABILITIES[3:]
    lines: list[str] = []
    for (lkey, llabel), (rkey, rlabel) in zip(left, right, strict=True):
        lscore = getattr(char, f"{lkey}_score")
        rscore = getattr(char, f"{rkey}_score")
        lmod = format_modifier(ability_modifier(lscore))
        rmod = format_modifier(ability_modifier(rscore))
        lines.append(
            f"{llabel} {lscore:>2} ({lmod})   {rlabel} {rscore:>2} ({rmod})"
        )
    return "```\n" + "\n".join(lines) + "\n```"


def _spellcasting_line(char: PlayerCharacter) -> str | None:
    if char.spell_ability is None:
        return None
    score = getattr(char, f"{char.spell_ability}_score")
    base = ability_modifier(score)
    total = spellcasting_modifier(score, char.spell_check_bonus)
    label = char.spell_ability.upper()
    detail = f"{label} {format_modifier(base)}"
    if char.spell_check_bonus:
        detail += f", talent {format_modifier(char.spell_check_bonus)}"
    return f"{format_modifier(total)}  ({detail})"


def _spell_list_value(spells: list[CharacterSpell]) -> str | None:
    """Known spells grouped by tier, e.g. `**Tier 1:** Light, Sleep`."""
    if not spells:
        return None
    by_tier: dict[int | None, list[str]] = {}
    for s in spells:
        by_tier.setdefault(s.display_tier, []).append(s.display_name)
    parts: list[str] = []
    for tier in sorted(by_tier, key=lambda t: (t is None, t or 0)):
        label = f"Tier {tier}" if tier is not None else "Other"
        parts.append(f"**{label}:** {', '.join(sorted(by_tier[tier]))}")
    return "\n".join(parts)


def build_spell_embed(spell: Spell) -> discord.Embed:
    """Reference detail for one spell (from the built-in list)."""
    classes = " / ".join(c.capitalize() for c in spell.class_list)
    embed = discord.Embed(title=spell.name, color=CHARACTER_COLOR)
    header = f"**Tier {spell.tier}** · {classes}"
    if spell.alignment:
        header += f" · {_ALIGNMENT_NAMES.get(spell.alignment, spell.alignment)}"
    lines = [header]
    if spell.duration:
        lines.append(f"**Duration:** {spell.duration}")
    if spell.range_:
        lines.append(f"**Range:** {spell.range_}")
    if spell.description:
        lines.append("")
        lines.append(spell.description)
    embed.description = "\n".join(lines)
    return embed


def build_character_spells_embed(
    char: PlayerCharacter, spells: list[CharacterSpell]
) -> discord.Embed:
    embed = discord.Embed(title=f"{char.name} — Spells", color=CHARACTER_COLOR)
    embed.description = _spell_list_value(spells) or "_(no spells known)_"
    return embed


def _character_header(char: PlayerCharacter) -> tuple[str, str]:
    """(title, subtitle) shown at the top of every character-sheet tab."""
    subtitle = f"Level {char.level}"
    if char.ancestry:
        subtitle += f" {char.ancestry}"
    if char.char_class:
        subtitle += f" {char.char_class}"
    if char.alignment:
        subtitle += f" · {char.alignment}"
    return char.name, subtitle


def build_combat_embed(
    char: PlayerCharacter, spells: list[CharacterSpell] | None = None
) -> discord.Embed:
    """Combat tab (home): HP/AC, ability scores, spellcasting, proficiencies, spells."""
    title, subtitle = _character_header(char)
    embed = discord.Embed(title=title, description=subtitle, color=CHARACTER_COLOR)

    hp = str(char.max_hp) if char.max_hp is not None else "—"
    # AC is the entered base (armor) plus the character's DEX modifier, shown
    # with the breakdown, e.g. "11 (10 + DEX)".
    if char.armor_class is not None:
        total = char.armor_class + ability_modifier(char.dex_score)
        ac = f"{total} ({char.armor_class} + DEX)"
    else:
        ac = "—"
    embed.add_field(
        name="Ability Scores",
        value=f"{_ability_table(char)}\n**HP** {hp}  •  **AC** {ac}",
        inline=False,
    )

    casting = _spellcasting_line(char)
    if casting is not None:
        embed.add_field(name="Spellcasting", value=casting, inline=False)

    if char.proficiencies:
        embed.add_field(
            name="Proficiencies", value=char.proficiencies[:1024], inline=False
        )

    spells = spells or []
    spell_value = _spell_list_value(spells)
    if spell_value is not None:
        embed.add_field(name="Spells", value=spell_value[:1024], inline=False)
    elif char.spell_ability is not None:
        embed.add_field(name="Spells", value="_(none known)_", inline=False)
    return embed


def _items_block(items: list[CharacterItem]) -> str:
    if not items:
        return "_(carrying nothing)_"
    lines = []
    for ci in items[:25]:
        tag = "catalog" if ci.is_catalog else "freeform"
        lines.append(
            f"• {ci.quantity}× {ci.display_name} "
            f"— {fmt_slots(ci.slot_cost)} slots _[{tag}]_"
        )
    if len(items) > 25:
        lines.append(f"…and {len(items) - 25} more")
    return "\n".join(lines)


def build_inventory_tab_embed(
    char: PlayerCharacter, items: list[CharacterItem]
) -> discord.Embed:
    """Inventory tab: carried items and gold."""
    title, subtitle = _character_header(char)
    embed = discord.Embed(title=title, description=subtitle, color=CHARACTER_COLOR)
    used = sum(ci.slot_cost for ci in items)
    free = carry_capacity(char.str_score)
    embed.add_field(name="Gold", value=format_cp(char.gold_cp) or "0cp", inline=False)
    embed.add_field(
        name=f"Items — {fmt_slots(used)}/{free} gear slots",
        value=_items_block(items),
        inline=False,
    )
    return embed


def build_roleplaying_embed(char: PlayerCharacter) -> discord.Embed:
    """Roleplaying tab: background, languages, talents."""
    title, subtitle = _character_header(char)
    embed = discord.Embed(title=title, description=subtitle, color=CHARACTER_COLOR)
    embed.add_field(
        name="Background", value=(char.background or "_(none)_")[:1024], inline=False
    )
    embed.add_field(
        name="Languages", value=(char.languages or "_(none)_")[:1024], inline=False
    )
    embed.add_field(
        name="Talents", value=(char.talents or "_(none)_")[:1024], inline=False
    )
    return embed


def build_character_inventory_embed(
    char: PlayerCharacter, items: list[CharacterItem]
) -> discord.Embed:
    used = sum(ci.slot_cost for ci in items)
    free = carry_capacity(char.str_score)
    embed = discord.Embed(
        title=f"{char.name} — Inventory", color=CHARACTER_COLOR
    )
    capacity_line = f"**Capacity:** {fmt_slots(used)}/{free} gear slots used"

    if not items:
        embed.description = f"{capacity_line}\n\n_(carrying nothing)_"
        return embed

    lines = [capacity_line, ""]
    for ci in items[:25]:
        tag = "catalog" if ci.is_catalog else "freeform"
        lines.append(
            f"• {ci.quantity}× {ci.display_name} "
            f"— {fmt_slots(ci.slot_cost)} slots _[{tag}]_"
        )
    if len(items) > 25:
        lines.append(f"…and {len(items) - 25} more")
    embed.description = "\n".join(lines)
    return embed


# ---------- Helpers ----------


COFFER_COLOR = discord.Color.dark_gold()


def build_coffer_show_embed(balance_cp: int) -> discord.Embed:
    embed = discord.Embed(title="Guild Coffers", color=COFFER_COLOR)
    embed.description = f"**Balance:** {_format_balance(balance_cp)}"
    return embed


def build_coffer_change_embed(
    *,
    action: str,
    delta_cp: int,
    new_balance_cp: int,
    reason: str | None = None,
    item_name: str | None = None,
) -> discord.Embed:
    """`action` is one of: 'added', 'subtracted', 'bought'."""
    embed = discord.Embed(title="Guild Coffers", color=COFFER_COLOR)
    delta_str = _format_balance(delta_cp)
    balance_str = _format_balance(new_balance_cp)

    if action == "added":
        first = f"**Added:** {delta_str}"
    elif action == "subtracted":
        first = f"**Subtracted:** {delta_str}"
    elif action == "bought":
        first = f"**Bought:** {item_name} for {delta_str}"
    else:
        first = f"**{action.title()}:** {delta_str}"

    lines = [first, f"**Balance:** {balance_str}"]
    if reason:
        lines.append(f"_Reason: {reason}_")
    embed.description = "\n".join(lines)
    return embed


def _format_balance(cp: int) -> str:
    formatted = format_cp(cp)
    return formatted if formatted is not None else "0cp"


def fmt_slots(n: float) -> str:
    return f"{n:g}"


def _fmt_number(n: float) -> str:
    return f"{n:g}"


def format_timesince(dt: datetime) -> str:
    """Render a datetime as 'X min ago' / 'Y hours ago' / 'Z days ago'."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    now = datetime.now(UTC)
    return _format_seconds(int((now - dt).total_seconds())) + " ago"


def format_duration(start: datetime, end: datetime) -> str:
    """Render the duration between two datetimes."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    return _format_seconds(int((end - start).total_seconds()))


def _format_seconds(seconds: int) -> str:
    if seconds < 60:
        return "less than a minute"
    if seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} min"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours} hour{'s' if hours != 1 else ''}"
    days = seconds // 86400
    return f"{days} day{'s' if days != 1 else ''}"
