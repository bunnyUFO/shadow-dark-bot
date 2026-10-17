"""`/character` for Dungeon Masters: whoever holds a DM_ROLE_IDS role can open
and edit any player's sheet; everyone else stays limited to their own."""

import asyncio

import pytest

from shadowdark_bot import storage
from shadowdark_bot.cogs import player_characters as pc
from shadowdark_bot.config import settings
from shadowdark_bot.dungeon_master import holds_dm_role
from shadowdark_bot.models import PlayerCharacter
from shadowdark_bot.services import characters

DM_ROLE = 77


class _Role:
    def __init__(self, role_id):
        self.id = role_id


class _Member:
    def __init__(self, uid, *role_ids, name=None):
        self.id = uid
        self.roles = [_Role(r) for r in role_ids]
        self.display_name = name or f"U{uid}"
        self.mention = f"<@{uid}>"


class _Resp:
    def __init__(self):
        self.sent = []  # (content, kwargs) per send_message
        self.edits = []  # kwargs per edit_message

    async def send_message(self, content=None, **kw):
        self.sent.append((content, kw))

    async def edit_message(self, **kw):
        self.edits.append(kw)


class _Interaction:
    def __init__(self, user):
        self.user = user
        self.response = _Resp()


BOB, AMY, DM = 1001, 1002, 1003


@pytest.fixture
def table(dbsession, monkeypatch):
    """Bob and Amy have characters; DM holds the DM role and has none."""
    monkeypatch.setattr(settings, "DM_ROLE_IDS", str(DM_ROLE))
    with dbsession() as s:
        for uid, name in ((BOB, "Bob"), (AMY, "Amy")):
            char = PlayerCharacter(user_id=str(uid), name=name, str_score=12)
            s.add(char)
            s.flush()
            storage.ensure_character_locations(s, char)
        s.commit()
    with dbsession() as s:
        characters.carry(s, str(AMY), item_name="Lockpicks", quantity=1, role="stash")
        s.commit()
    return {"bob": _Member(BOB, 5, name="Bob"), "amy": _Member(AMY, name="Amy"),
            "dm": _Member(DM, 5, DM_ROLE, name="Travis")}


def _sheet(member, interaction_user):
    """Run `/character sheet member:<member>` as `interaction_user`."""
    i = _Interaction(interaction_user)
    cog = pc.PlayerCharacters(bot=None)
    asyncio.run(pc.PlayerCharacters.sheet.callback(cog, i, member))
    return i.response.sent[-1]


# ---------- who is a DM ----------


def test_holds_dm_role():
    assert holds_dm_role(_Member(1, 5, DM_ROLE), {DM_ROLE}) is True
    assert holds_dm_role(_Member(1, 5), {DM_ROLE}) is False
    assert holds_dm_role(_Member(1, DM_ROLE), set()) is False  # no DM role configured
    assert holds_dm_role(object(), {DM_ROLE}) is False  # a plain user has no roles


def test_holds_dm_role_reads_settings(monkeypatch):
    monkeypatch.setattr(settings, "DM_ROLE_IDS", f"{DM_ROLE}, 88")
    assert holds_dm_role(_Member(1, 88)) is True
    monkeypatch.setattr(settings, "DM_ROLE_IDS", "")
    assert holds_dm_role(_Member(1, 88)) is False


# ---------- /character sheet member: ----------


def test_player_cannot_open_someone_else(table):
    content, kw = _sheet(table["amy"], table["bob"])
    assert content == pc.NOT_A_DM and "view" not in kw


def test_naming_yourself_opens_your_own_sheet(table):
    content, kw = _sheet(table["bob"], table["bob"])
    assert content is None and kw["view"].user_id == str(BOB)


def test_dm_opens_another_players_sheet_for_editing(table):
    content, kw = _sheet(table["amy"], table["dm"])
    assert "Editing **Amy**'s character as Dungeon Master" in content
    view = kw["view"]
    assert isinstance(view, pc.CharacterSheetView) and view.user_id == str(AMY)
    assert kw["ephemeral"] is True

    # every action of that sheet acts on Amy, and the stash is part of it
    async def inventory():
        return pc._build_sheet_payload(view.user_id, "inventory")

    embed, inv_view = asyncio.run(inventory())
    assert "Lockpicks" in str(embed.to_dict())
    labels = {getattr(c, "label", None) for c in inv_view.children}
    assert {"Manage Held", "Manage Stash", "Edit Gold"} <= labels


def test_dm_cannot_create_a_character_for_someone(table):
    content, kw = _sheet(_Member(4242, name="Newbie"), table["dm"])
    assert content == "**Newbie** doesn't have a character yet." and "view" not in kw


# ---------- buttons on the sheet ----------


def _check(view_owner, clicker):
    async def go():
        view = pc.CharacterSheetView(str(view_owner))
        i = _Interaction(clicker)
        return await view.interaction_check(i), i.response.sent

    return asyncio.run(go())


def test_owner_and_dm_may_press_the_buttons(table):
    assert _check(AMY, table["amy"]) == (True, [])
    assert _check(AMY, table["dm"]) == (True, [])


def test_other_players_may_not(table):
    allowed, sent = _check(AMY, table["bob"])
    assert allowed is False and sent[0][0] == "This isn't your character sheet."


def test_losing_the_role_locks_the_dm_out(table, monkeypatch):
    monkeypatch.setattr(settings, "DM_ROLE_IDS", "")
    assert _check(AMY, table["dm"])[0] is False


def test_dm_edit_saves_to_the_owner(table, dbsession):
    async def save_gold():
        modal = pc.EditGoldModal(str(AMY), gold="3gp")
        i = _Interaction(table["dm"])
        await modal.on_submit(i)
        return i

    i = asyncio.run(save_gold())
    assert i.response.edits and "content" not in i.response.edits[0]  # DM banner kept
    with dbsession() as s:
        assert characters.load_character(s, str(AMY)).gold_cp == 300
        assert characters.load_character(s, str(BOB)).gold_cp == 0


def test_delete_message_names_whose_character(table):
    async def delete(clicker):
        view = pc.DeleteConfirmView(str(AMY))
        i = _Interaction(clicker)
        await view.confirm.callback(i)
        return i.response.edits[0]["content"]

    assert asyncio.run(delete(table["dm"])) == "The character has been deleted."
