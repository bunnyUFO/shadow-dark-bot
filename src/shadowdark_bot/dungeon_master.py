"""Who is a Dungeon Master: whoever holds one of the DM_ROLE_IDS roles.

A DM may edit every character, in the web apps and through `/character`;
everyone else only their own."""

from shadowdark_bot.config import settings


def holds_dm_role(member, role_ids: set[int] | None = None) -> bool:
    """Does this Discord member hold a DM role? `role_ids` defaults to the
    configured DM_ROLE_IDS. A plain user (no guild, e.g. a DM channel) has no
    roles, so never is one."""
    if role_ids is None:
        role_ids = settings.dm_role_ids
    return any(role.id in role_ids for role in getattr(member, "roles", ()))
