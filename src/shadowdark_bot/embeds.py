from datetime import datetime, timezone

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
    InventoryEntry,
    Item,
    Location,
    TreasuryEntry,
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


def build_item_embed(item: Item) -> discord.Embed:
    label, color = _ITEM_TYPE_DISPLAY.get(
        item.item_type, ("Common", discord.Color.blue())
    )
    embed = discord.Embed(title=item.name, color=color)

    lines: list[str] = []
    if item.description:
        lines.append(item.description)
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
        line = f"• {stack.quantity}× {stack.item.name}"
        if stack.notes:
            line += f" — _{stack.notes}_"
        lines.append(line)
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
    *,
    borrower_mention: str | None = None,
    notes: str | None = None,
    held_for: str | None = None,
) -> discord.Embed:
    title = f"#{entry.id}  {entry.item.name}"
    if entry.tag:
        title += f" ({entry.tag})"

    embed = discord.Embed(title=title, color=TREASURY_COLOR)

    lines: list[str] = []
    if action == "added":
        lines.append(f"Added to the treasury by {actor_mention}.")
    elif action == "removed":
        lines.append(f"Removed from the treasury by {actor_mention}.")
    elif action == "borrowed":
        if borrower_mention and borrower_mention != actor_mention:
            lines.append(f"Borrowed by {borrower_mention}.")
            lines.append(f"_(checked out by {actor_mention})_")
        else:
            lines.append(f"Borrowed by {actor_mention}.")
    elif action == "returned":
        clause = f"Returned by {actor_mention}."
        if borrower_mention and held_for:
            clause += f"\n{borrower_mention} had it for {held_for}."
        elif held_for:
            clause += f"\nHeld for {held_for}."
        lines.append(clause)

    if notes:
        lines.append(f"_Notes: {notes}_")

    embed.description = "\n".join(lines)
    return embed


def build_treasury_info_embed(
    entry: TreasuryEntry,
    open_borrow: Borrow | None = None,
) -> discord.Embed:
    """Static info embed for one treasury entry — used by /treasury info and
    by the interactive list's drill-down. Mirrors build_item_embed's fields
    plus current status."""
    title = f"#{entry.id}  {entry.item.name}"
    if entry.tag:
        title += f" ({entry.tag})"
    embed = discord.Embed(title=title, color=TREASURY_COLOR)

    lines: list[str] = []
    if entry.item.description:
        lines.append(entry.item.description)
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


def build_who_has_embed(
    item: Item,
    pairs: list[tuple[TreasuryEntry, Borrow | None]],
) -> discord.Embed:
    plural = "instance" if len(pairs) == 1 else "instances"
    embed = discord.Embed(
        title=f"{item.name} — {len(pairs)} {plural}",
        color=TREASURY_COLOR,
    )

    if not pairs:
        embed.description = "_No treasury instances of this item._"
        return embed

    lines: list[str] = []
    for entry, borrow_row in pairs:
        tag_str = f" ({entry.tag})" if entry.tag else " (no tag)"
        if borrow_row is None:
            lines.append(f"• #{entry.id}{tag_str} — available")
        else:
            line = (
                f"• #{entry.id}{tag_str} — borrowed by <@{borrow_row.borrower_id}>, "
                f"{format_timesince(borrow_row.borrowed_at)}"
            )
            if borrow_row.notes:
                line += f"\n   _{borrow_row.notes}_"
            lines.append(line)
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
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    return _format_seconds(int((now - dt).total_seconds())) + " ago"


def format_duration(start: datetime, end: datetime) -> str:
    """Render the duration between two datetimes."""
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)
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
