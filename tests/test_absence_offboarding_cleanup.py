"""Regression tests: Abmeldungen enden automatisch bei Team-Offboarding und Serveraustritt.

Nur synthetische Daten, temporäre SQLite-Datenbanken und Discord-Fakes – keine echten Discord-Aktionen.
"""
import sqlite3
import tempfile
from contextlib import closing
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import webserver
from cogs import abmeldung

GUILD_ID = 111
OTHER_GUILD_ID = 222
USER_ID = 3001
TEAM_ROLE_A, TEAM_ROLE_B = 2001, 2002
ABSENT_ROLE_ID = 5001
CITIZEN_ID = 1531303733311307997
EX_TEAM_ID = 1489912887949394082


def _date(days: int) -> str:
    return (datetime.now() + timedelta(days=days)).strftime("%d.%m.%Y")


class FakeRole:
    def __init__(self, role_id, position=1, *, default=False, managed=False):
        self.id = role_id
        self.position = position
        self.name = f"role-{role_id}"
        self.managed = managed
        self._default = default

    def is_default(self):
        return self._default

    def __eq__(self, other):
        return isinstance(other, FakeRole) and other.id == self.id

    def __hash__(self):
        return hash(self.id)


class FakeMember:
    def __init__(self, guild, roles, nick=None, member_id=USER_ID, edit_error=None):
        self.guild = guild
        self.id = member_id
        self.bot = False
        self.roles = list(roles)
        self.nick = nick
        self.display_name = nick or "Max"
        self.top_role = max(self.roles, key=lambda r: r.position)
        self.edit_error = edit_error
        self.edit_calls = []
        self.add_roles = AsyncMock()
        self.remove_roles = AsyncMock(side_effect=self._remove_roles)

    async def _remove_roles(self, *roles, reason=None):
        self.roles = [r for r in self.roles if r not in roles]

    async def edit(self, **kwargs):
        if self.edit_error:
            raise self.edit_error
        self.edit_calls.append(kwargs)
        if "roles" in kwargs:
            self.roles = [FakeRole(1, 0, default=True), *kwargs["roles"]]
        if "nick" in kwargs:
            self.nick = kwargs["nick"]
            self.display_name = self.nick or "Max"


class FakeGuild:
    def __init__(self, guild_id=GUILD_ID):
        self.id = guild_id
        self.owner_id = 1
        self.members = {}
        self.roles = {}
        self.me = SimpleNamespace(
            top_role=FakeRole(900, 100),
            guild_permissions=SimpleNamespace(administrator=True, manage_nicknames=True, manage_roles=True),
        )
        self.fetch_member = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "unknown"))

    def get_member(self, member_id):
        return self.members.get(member_id)

    def get_role(self, role_id):
        return self.roles.get(role_id)


class AbsenceCleanupBase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.db = str(Path(tmp.name) / "abmeldungen.db")
        patcher = patch.object(abmeldung, "DB_NAME", self.db)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.guild = FakeGuild(GUILD_ID)
        self.other_guild = FakeGuild(OTHER_GUILD_ID)
        self.guild.roles[ABSENT_ROLE_ID] = FakeRole(ABSENT_ROLE_ID, 3)
        self.bot = SimpleNamespace(
            guilds=[self.guild, self.other_guild],
            user=SimpleNamespace(id=999),
            get_guild=lambda gid: {GUILD_ID: self.guild, OTHER_GUILD_ID: self.other_guild}.get(gid),
        )
        self.cog = object.__new__(abmeldung.AbmeldungCog)
        self.cog.bot = self.bot
        self.cog._nickname_permission_warnings = set()
        self.cog._nickname_forbidden = set()
        self.cog.init_db()
        self.cog.set_config("abgemeldet_role_id", ABSENT_ROLE_ID)
        self.cog.update_live_list = AsyncMock()
        self.cog.trigger_teamlist_update = AsyncMock()

        cfg = patch.object(webserver, "load_config", return_value={"team_role_ids": [TEAM_ROLE_A, TEAM_ROLE_B]})
        cfg.start()
        self.addCleanup(cfg.stop)

    def add_absence(self, von=-1, bis=3, guild_id=GUILD_ID, user_id=USER_ID, original_nick="Max"):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(
                "INSERT OR REPLACE INTO abmeldungen (user_id,user_name,grund,von,bis,original_nick,guild_id)"
                " VALUES (?,?,?,?,?,?,?)",
                (user_id, "Max", "privat", _date(von), _date(bis), original_nick, guild_id),
            )

    def rows(self):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT user_id, guild_id FROM abmeldungen").fetchall()

    def make_member(self, roles, nick="Max | Abgemeldet", guild=None, **kw):
        guild = guild or self.guild
        member = FakeMember(guild, [FakeRole(1, 0, default=True), *roles], nick=nick, **kw)
        guild.members[member.id] = member
        return member


class TeamKickCleanupTests(AbsenceCleanupBase):
    def setUp(self):
        super().setUp()
        self.guild.roles.update({
            CITIZEN_ID: FakeRole(CITIZEN_ID, 10), EX_TEAM_ID: FakeRole(EX_TEAM_ID, 11),
            TEAM_ROLE_A: FakeRole(TEAM_ROLE_A, 20), TEAM_ROLE_B: FakeRole(TEAM_ROLE_B, 21),
        })
        self.guild.me.guild_permissions.manage_roles = True
        previous = getattr(webserver.app.state, "bot", None)
        webserver.app.state.bot = SimpleNamespace(get_cog=lambda n: self.cog if n == "AbmeldungCog" else None)
        self.addCleanup(setattr, webserver.app.state, "bot", previous)

    def member(self, **kw):
        roles = [self.guild.roles[i] for i in (CITIZEN_ID, TEAM_ROLE_A, TEAM_ROLE_B)] + [self.guild.roles[ABSENT_ROLE_ID]]
        return self.make_member(roles, **kw)

    async def kick(self, member):
        return await webserver.apply_team_kick_role_reset(self.guild, member, {}, "Test")

    async def test_active_absence_removed_and_lists_updated(self):  # 1, 14, 18
        self.add_absence()
        member = self.member()
        result = await self.kick(member)
        self.assertTrue(result["ok"])
        self.assertEqual(self.rows(), [])
        self.assertEqual(member.nick, "Max")  # Suffix bereinigt
        self.cog.update_live_list.assert_awaited()
        self.cog.trigger_teamlist_update.assert_awaited_with(self.guild)

    async def test_future_absence_removed(self):  # 2
        self.add_absence(von=5, bis=9)
        await self.kick(self.member())
        self.assertEqual(self.rows(), [])

    async def test_no_absence_is_noop(self):  # 3
        result = await self.kick(self.member())
        self.assertTrue(result["ok"])
        self.cog.update_live_list.assert_not_awaited()

    async def test_failed_team_kick_keeps_absence(self):  # 4
        self.add_absence()
        member = self.member(edit_error=discord.Forbidden(MagicMock(status=403), "no"))
        result = await self.kick(member)
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.rows()), 1)
        self.cog.update_live_list.assert_not_awaited()

    async def test_hierarchy_block_keeps_absence(self):  # 4 (Rollenhierarchie)
        self.add_absence()
        self.guild.me.top_role = FakeRole(900, 5)
        result = await self.kick(self.member())
        self.assertFalse(result["ok"])
        self.assertEqual(len(self.rows()), 1)

    async def test_cleanup_failure_does_not_break_team_kick(self):
        self.add_absence()
        self.cog.cleanup_member_absence = AsyncMock(side_effect=RuntimeError("boom"))
        result = await self.kick(self.member())
        self.assertTrue(result["ok"])

    async def test_missing_absence_role_no_error(self):  # 13
        self.add_absence()
        member = self.member()
        del self.guild.roles[ABSENT_ROLE_ID]
        result = await self.kick(member)
        self.assertTrue(result["ok"])
        self.assertEqual(self.rows(), [])


class RoleLossTests(AbsenceCleanupBase):
    async def update(self, before_roles, after_roles):
        before = SimpleNamespace(id=USER_ID, guild=self.guild, bot=False, roles=before_roles)
        member = self.make_member(after_roles)
        after = SimpleNamespace(id=USER_ID, guild=self.guild, bot=False, roles=member.roles)
        await self.cog.on_member_update(before, after)
        return member

    async def test_losing_only_one_of_several_team_roles_keeps_absence(self):  # 5
        self.add_absence()
        a, b = FakeRole(TEAM_ROLE_A), FakeRole(TEAM_ROLE_B)
        await self.update([a, b], [b])
        self.assertEqual(len(self.rows()), 1)

    async def test_losing_last_team_role_ends_absence(self):  # 6, 14
        self.add_absence()
        a = FakeRole(TEAM_ROLE_A)
        member = await self.update([a, self_role := FakeRole(ABSENT_ROLE_ID)], [self_role])
        self.assertEqual(self.rows(), [])
        self.assertEqual(member.nick, "Max")
        self.cog.trigger_teamlist_update.assert_awaited()

    async def test_unrelated_role_change_keeps_absence(self):  # 7
        self.add_absence()
        a, other = FakeRole(TEAM_ROLE_A), FakeRole(7777)
        await self.update([a], [a, other])
        await self.update([other], [])  # nie im Team gewesen
        self.assertEqual(len(self.rows()), 1)


class LeaveTests(AbsenceCleanupBase):
    def departed(self):
        return SimpleNamespace(id=USER_ID, guild=self.guild, bot=False)

    async def test_leave_removes_absence_without_discord_changes(self):  # 8, 15
        self.add_absence()
        ghost = self.departed()
        await self.cog.on_member_remove(ghost)
        self.assertEqual(self.rows(), [])
        self.guild.fetch_member.assert_not_awaited()
        self.cog.update_live_list.assert_awaited()
        self.cog.trigger_teamlist_update.assert_not_awaited()

    async def test_kick_event_removes_absence(self):  # 9 (Kick löst on_member_remove aus)
        self.add_absence(von=4, bis=8)
        await self.cog.on_member_remove(self.departed())
        self.assertEqual(self.rows(), [])

    async def test_ban_removes_absence(self):  # 10
        self.add_absence()
        await self.cog.on_member_ban(self.guild, SimpleNamespace(id=USER_ID, bot=False))
        self.assertEqual(self.rows(), [])

    async def test_repeated_events_are_idempotent(self):  # 11
        self.add_absence()
        await self.cog.on_member_remove(self.departed())
        await self.cog.on_member_ban(self.guild, SimpleNamespace(id=USER_ID, bot=False))
        await self.cog.on_member_remove(self.departed())
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.cog.update_live_list.await_count, 1)

    async def test_other_guild_data_untouched(self):  # 12
        self.add_absence(guild_id=OTHER_GUILD_ID)
        await self.cog.on_member_remove(self.departed())
        self.assertEqual(self.rows(), [(USER_ID, OTHER_GUILD_ID)])

    async def test_legacy_row_without_guild_protected_when_member_elsewhere(self):  # 12
        self.add_absence(guild_id=None)
        self.make_member([], guild=self.other_guild)
        await self.cog.on_member_remove(self.departed())
        self.assertEqual(len(self.rows()), 1)

    async def test_legacy_row_without_guild_removed_when_not_elsewhere(self):
        self.add_absence(guild_id=None)
        await self.cog.on_member_remove(self.departed())
        self.assertEqual(self.rows(), [])

    async def test_invalid_guild_id_deletes_nothing(self):
        self.add_absence()
        self.assertFalse(await self.cog.cleanup_member_absence(SimpleNamespace(id=None), USER_ID, member_present=False, source="t"))
        self.assertFalse(await self.cog.cleanup_member_absence(None, USER_ID, member_present=False, source="t"))
        self.assertEqual(len(self.rows()), 1)

    async def test_database_error_is_handled(self):
        self.add_absence()
        with patch.object(abmeldung.sqlite3, "connect", side_effect=sqlite3.OperationalError("locked")):
            self.assertFalse(await self.cog.cleanup_member_absence(self.guild, USER_ID, member_present=True, source="t"))
        self.assertEqual(len(self.rows()), 1)
        self.cog.update_live_list.assert_not_awaited()

    async def test_discord_failure_after_db_cleanup_is_contained(self):
        self.add_absence()
        self.make_member([FakeRole(ABSENT_ROLE_ID)], edit_error=discord.Forbidden(MagicMock(status=403), "no"))
        self.cog.reset_user_status = AsyncMock(side_effect=RuntimeError("api down"))
        self.assertTrue(await self.cog.cleanup_member_absence(self.guild, USER_ID, member_present=True, source="t"))
        self.assertEqual(self.rows(), [])
        self.cog.update_live_list.assert_awaited()


class ExistingBehaviourTests(AbsenceCleanupBase):
    async def test_automatic_expiry_still_works(self):  # 16
        self.add_absence(von=-10, bis=-2)
        self.add_absence(user_id=3002, von=-1, bis=4)
        self.cog.sync_existing_abmeldungen = AsyncMock()
        self.cog.reset_user_status = AsyncMock()
        await self.cog._check_expired_abmeldungen_once()
        self.assertEqual([r[0] for r in self.rows()], [3002])
        self.cog.reset_user_status.assert_awaited_once()
        self.cog.update_live_list.assert_awaited()

    async def test_manual_return_unchanged(self):  # 17
        self.add_absence()
        self.cog.reset_user_status = AsyncMock()
        interaction = SimpleNamespace(
            user=SimpleNamespace(id=USER_ID), guild=self.guild, guild_id=GUILD_ID,
            response=SimpleNamespace(is_done=lambda: False, defer=AsyncMock(), send_message=AsyncMock()),
            followup=SimpleNamespace(send=AsyncMock()),
        )
        await self.cog.abmeldung_entfernen_fuer_user(interaction)
        self.assertEqual(self.rows(), [])
        self.cog.reset_user_status.assert_awaited_once()
        self.cog.trigger_teamlist_update.assert_awaited_with(self.guild)
        self.assertIn("Willkommen zurück", interaction.followup.send.await_args.args[0])

    def test_custom_ids_unchanged(self):
        ids = {item.custom_id for item in abmeldung.AbmeldungView(self.cog).children}
        self.assertEqual(ids, {"pulse_abmeldung:abmelden", "pulse_abmeldung:anmelden"})


if __name__ == "__main__":
    unittest.main()

