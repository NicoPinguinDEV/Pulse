"""A-08: shared Discord fakes for tests – no network, no real discord.py objects.

Covers what Pulse code typically touches: guild lookups, members with roles and permissions,
text channels that record sent messages, and interactions with response/followup.
"""
from __future__ import annotations

from types import SimpleNamespace


class FakeRole:
    def __init__(self, rid: int, name: str = "Rolle", position: int = 1, managed: bool = False, mentionable: bool = True):
        self.id, self.name, self.position, self.managed, self.mentionable = rid, name, position, managed, mentionable
        self.mention = f"<@&{rid}>"
        self.members: list = []
        self.color = SimpleNamespace(value=0x5B5CF0)  # discord.Colour stand-in (role_hex, team list)

    def is_default(self) -> bool:
        return False


class FakeMember:
    def __init__(self, uid: int, name: str = "Max", roles=(), admin: bool = False, bot: bool = False):
        self.id, self.display_name, self.name, self.bot = uid, name, name.lower(), bot
        self.roles = list(roles)
        self.mention = f"<@{uid}>"
        self.guild_permissions = SimpleNamespace(administrator=admin, manage_roles=admin)
        self.avatar = None
        self.display_avatar = SimpleNamespace(url="https://cdn.example/avatar.png")
        self.voice = None
        self.added_roles, self.removed_roles, self.sent = [], [], []

    async def add_roles(self, *roles, reason=None):
        self.added_roles.extend(roles)
        self.roles.extend(r for r in roles if r not in self.roles)

    async def remove_roles(self, *roles, reason=None):
        self.removed_roles.extend(roles)
        self.roles = [r for r in self.roles if r not in roles]

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


class FakeMessage:
    def __init__(self, mid: int, channel, content=None, **kwargs):
        self.id, self.channel, self.content, self.kwargs, self.deleted = mid, channel, content, kwargs, False

    async def delete(self, **_):
        self.deleted = True


class FakeChannel:
    def __init__(self, cid: int = 555, name: str = "kanal", position: int = 1):
        self.id, self.name, self.position = cid, name, position
        self.mention = f"<#{cid}>"
        self.sent: list[FakeMessage] = []
        self.messages: dict[int, FakeMessage] = {}

    async def send(self, content=None, **kwargs):
        message = FakeMessage(900 + len(self.sent), self, content, **kwargs)
        self.sent.append(message)
        self.messages[message.id] = message
        return message

    async def fetch_message(self, mid):
        if mid not in self.messages:
            import discord
            raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone")
        return self.messages[mid]


class FakeGuild:
    def __init__(self, gid: int = 1, members=(), roles=(), channels=(), owner_id: int = 0, name: str = "© BOCHUM RP | V1"):
        self.id, self.name, self.owner_id = gid, name, owner_id
        self.members, self.roles = list(members), list(roles)
        self.text_channels = list(channels)
        self.voice_channels: list = []
        self.channels = list(channels)
        self.member_count = len(self.members)
        self.icon = None
        self.me = FakeMember(999, "Pulse", admin=True, bot=True)

    def get_member(self, uid):
        return next((m for m in self.members if m.id == int(uid)), None)

    def get_role(self, rid):
        return next((r for r in self.roles if r.id == int(rid)), None)

    def get_channel(self, cid):
        return next((c for c in self.channels if c.id == int(cid)), None)


class FakeResponse:
    def __init__(self, log: list):
        self.log, self.done, self.modal = log, False, None

    def is_done(self) -> bool:
        return self.done

    async def send_message(self, content=None, ephemeral=False, **kwargs):
        self.done = True
        self.log.append(("response", content, ephemeral))

    async def defer(self, **_):
        self.done = True

    async def edit_message(self, **kwargs):
        self.done = True
        self.log.append(("edit", kwargs.get("content"), True))

    async def send_modal(self, modal):
        self.done = True
        self.modal = modal


class FakeInteraction:
    """Records every answer in `.log` as (kind, text, ephemeral)."""

    def __init__(self, user: FakeMember, guild: FakeGuild | None = None, channel: FakeChannel | None = None):
        self.user, self.guild = user, guild
        self.channel = channel or FakeChannel(777, "panel")
        self.log: list = []
        self.response = FakeResponse(self.log)
        self.followup = SimpleNamespace(send=self._followup)
        self.message = None
        self.command = SimpleNamespace(qualified_name="test")

    async def _followup(self, content=None, ephemeral=False, **kwargs):
        self.log.append(("followup", content, ephemeral))

    async def edit_original_response(self, **kwargs):
        self.log.append(("edit_original", kwargs.get("content"), True))

    def texts(self) -> list[str]:
        return [entry[1] for entry in self.log if entry[1]]
