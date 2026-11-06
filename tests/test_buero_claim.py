"""Office requests: nobody takes their own request, and each request is taken exactly once."""
import asyncio
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

from cogs import buero_system as bs

WAITING_ROOM = 100
OFFICE = 200
PING_CHANNEL = 300
GUILD = 1
TEAM_ROLE = 42


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), "x")


class Voice(discord.VoiceChannel):
    """Real isinstance match for the production check, without any gateway state."""

    def __init__(self, cid, name="Büro", *, allowed=True):
        self.id = cid
        self.name = name
        self.allowed = allowed

    def permissions_for(self, _member):
        return SimpleNamespace(move_members=self.allowed, connect=self.allowed)


class Member:
    def __init__(self, uid, *, team=False, admin=False, channel=None):
        self.id = uid
        self.mention = f"<@{uid}>"
        self.roles = [SimpleNamespace(id=TEAM_ROLE)] if team else []
        self.guild_permissions = SimpleNamespace(administrator=admin)
        self.voice = SimpleNamespace(channel=channel) if channel else None
        self.moves = []
        self.move_error = None

    async def move_to(self, channel, reason=None):
        if self.move_error:
            raise self.move_error
        self.moves.append(channel.id)
        self.voice = SimpleNamespace(channel=channel)

    def __str__(self):
        return f"member-{self.id}"


class Message:
    def __init__(self, mid):
        self.id = mid
        self.deleted = 0

    async def delete(self):
        self.deleted += 1


class Response:
    def __init__(self, log):
        self.log = log
        self.done = False

    def is_done(self):
        return self.done

    async def send_message(self, text, ephemeral=False, **_):
        self.done = True
        self.log.append(text)

    async def defer(self, **_):
        self.done = True


class Interaction:
    def __init__(self, user, guild, message, channel_id=PING_CHANNEL):
        self.user = user
        self.guild = guild
        self.message = message
        self.channel_id = channel_id
        self.texts = []
        self.response = Response(self.texts)
        self.followup = SimpleNamespace(send=self._followup)

    async def _followup(self, text, ephemeral=False, **_):
        self.texts.append(text)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        db = patch.object(bs, "DB_NAME", str(Path(self.tmp.name) / "buero.db"))
        db.start()
        self.addCleanup(db.stop)
        team = patch.object(bs, "_is_office_team", lambda m: m.guild_permissions.administrator or bool(m.roles))
        team.start()
        self.addCleanup(team.stop)
        bs.set_config("warteraum_channel_id", WAITING_ROOM)
        self.room = Voice(WAITING_ROOM, "Warteraum")
        self.office = Voice(OFFICE, "Leitung")
        self.waiting = Member(7, team=True, channel=self.room)
        self.colleague = Member(8, team=True)
        self.other = Member(9, team=True)
        self.members = {7: self.waiting, 8: self.colleague, 9: self.other}
        self.channels = {OFFICE: self.office, WAITING_ROOM: self.room}
        self.guild = SimpleNamespace(id=GUILD, me=object(), get_member=self.members.get, get_channel=self.channels.get)
        self.message = Message(5000)
        self.bot = SimpleNamespace(get_channel=lambda _: None)
        bs.save_pending_request(7, GUILD, PING_CHANNEL, self.message.id, OFFICE)
        self.view = bs.BueroUebernehmenView(self.bot, 0, 0)

    def click(self, user, message=None, **kwargs):
        interaction = Interaction(user, self.guild, message or self.message, **kwargs)
        asyncio.run(self.view.uebernehmen.callback(interaction))
        return interaction

    def row(self):
        return bs.get_pending_request(7)


class SelfClaim(Base):
    def test_team_member_cannot_take_own_request(self):
        result = self.click(self.waiting)
        self.assertEqual(result.texts, [bs.SELF_CLAIM_MESSAGE])
        self.assertIn("Du kannst deine eigene Büro-Anfrage nicht übernehmen.", bs.SELF_CLAIM_MESSAGE)
        self.assertEqual(self.waiting.moves, [])
        self.assertIsNone(self.row()["claimed_by"])
        self.assertEqual(self.message.deleted, 0)

    def test_admin_cannot_take_own_request_either(self):
        self.waiting.guild_permissions.administrator = True
        self.assertEqual(self.click(self.waiting).texts, [bs.SELF_CLAIM_MESSAGE])
        self.assertEqual(self.waiting.moves, [])

    def test_view_built_for_the_user_gives_no_way_around_it(self):
        self.view = bs.BueroUebernehmenView(self.bot, 7, OFFICE)
        self.assertEqual(self.click(self.waiting).texts, [bs.SELF_CLAIM_MESSAGE])
        self.assertEqual(self.waiting.moves, [])

    def test_database_refuses_self_claim_on_its_own(self):
        self.assertEqual(bs.claim_pending_request(self.message.id, GUILD, 7), "self")
        self.assertIsNone(self.row()["claimed_by"])


class ForeignClaim(Base):
    def test_colleague_takes_request(self):
        result = self.click(self.colleague)
        self.assertEqual(self.waiting.moves, [OFFICE])
        self.assertIn("erfolgreich", result.texts[-1])
        self.assertEqual(self.message.deleted, 1)
        self.assertIsNone(self.row())

    def test_ordinary_user_is_refused(self):
        stranger = Member(50)
        self.members[50] = stranger
        result = self.click(stranger)
        self.assertIn("Nur Teammitglieder", result.texts[0])
        self.assertEqual(self.waiting.moves, [])
        self.assertIsNone(self.row()["claimed_by"])

    def test_foreign_guild_channel_or_user_counts_as_inactive(self):
        self.guild.id = 2
        self.assertIn("nicht mehr aktiv", self.click(self.colleague).texts[0])
        self.guild.id = GUILD
        self.assertIn("nicht mehr aktiv", self.click(self.colleague, channel_id=999).texts[0])
        self.view = bs.BueroUebernehmenView(self.bot, 8, OFFICE)
        self.assertIn("nicht mehr aktiv", self.click(self.colleague).texts[0])
        self.assertEqual(self.waiting.moves, [])
        self.assertIsNotNone(self.row())


class Concurrency(Base):
    def test_only_one_of_two_claims_wins(self):
        results = [bs.claim_pending_request(self.message.id, GUILD, uid) for uid in (8, 9)]
        self.assertEqual(results, ["ok", "taken"])
        self.assertEqual(self.row()["claimed_by"], 8)

    def test_two_clicks_during_a_slow_move_move_once(self):
        moved = []

        async def scenario():
            gate = asyncio.Event()

            async def slow_move(channel, reason=None):
                moved.append(channel.id)
                await gate.wait()

            self.waiting.move_to = slow_move
            first = Interaction(self.colleague, self.guild, self.message)
            second = Interaction(self.other, self.guild, self.message)
            task = asyncio.create_task(self.view.uebernehmen.callback(first))
            await asyncio.sleep(0)
            await self.view.uebernehmen.callback(second)
            gate.set()
            await task
            return second

        second = asyncio.run(scenario())
        self.assertEqual(moved, [OFFICE])
        self.assertIn("bereits", second.texts[0])

    def test_finish_button_waits_for_running_claim(self):
        bs.claim_pending_request(self.message.id, GUILD, 9)
        interaction = Interaction(self.colleague, self.guild, self.message)
        asyncio.run(self.view.auftrag_erledigt.callback(interaction))
        self.assertIn("gerade übernommen", interaction.texts[0])
        self.assertIsNotNone(self.row())

    def test_new_ping_does_not_inherit_a_claim(self):
        bs.claim_pending_request(self.message.id, GUILD, 8)
        bs.save_pending_request(7, GUILD, PING_CHANNEL, 5001, OFFICE)
        self.assertIsNone(self.row()["claimed_by"])


class Failures(Base):
    def test_missing_request_removes_outdated_ping(self):
        stale = Message(1)
        result = self.click(self.colleague, message=stale)
        self.assertIn("nicht mehr aktiv", result.texts[0])
        self.assertEqual(stale.deleted, 1)
        self.assertEqual(self.waiting.moves, [])

    def test_waiting_person_left(self):
        self.waiting.voice = None
        result = self.click(self.colleague)
        self.assertIn("wartet nicht mehr", result.texts[0])
        self.assertIsNone(self.row())
        self.assertEqual(self.message.deleted, 1)

    def test_office_gone_and_team_member_not_in_voice(self):
        del self.channels[OFFICE]
        result = self.click(self.colleague)
        self.assertIn("existiert nicht mehr", result.texts[0])
        self.assertIsNone(self.row()["claimed_by"])

    def test_office_gone_falls_back_to_team_members_voice(self):
        del self.channels[OFFICE]
        self.colleague.voice = SimpleNamespace(channel=Voice(201, "Andere"))
        self.click(self.colleague)
        self.assertEqual(self.waiting.moves, [201])

    def test_waiting_room_is_no_target(self):
        bs.save_pending_request(7, GUILD, PING_CHANNEL, self.message.id, 0)
        self.colleague.voice = SimpleNamespace(channel=self.room)
        result = self.click(self.colleague)
        self.assertIn("kein Büro", result.texts[0])
        self.assertEqual(self.waiting.moves, [])

    def test_bot_without_permission_does_not_claim(self):
        self.office.allowed = False
        result = self.click(self.colleague)
        self.assertIn("Rechte", result.texts[0])
        self.assertIsNone(self.row()["claimed_by"])

    def test_discord_refuses_move_and_request_stays_claimable(self):
        for error in (http_error(discord.Forbidden, 403), http_error(discord.HTTPException, 500), RuntimeError("x")):
            self.waiting.move_error = error
            result = self.click(self.colleague)
            self.assertIn("Die Anfrage bleibt offen", result.texts[-1])
            self.assertIsNone(self.row()["claimed_by"])
            self.assertEqual(self.message.deleted, 0)
        self.waiting.move_error = None
        self.click(self.other)
        self.assertEqual(self.waiting.moves, [OFFICE])

    def test_restart_clears_stale_claim_and_stateless_view_works(self):
        bs.claim_pending_request(self.message.id, GUILD, 9)  # crashed during the move
        cog = SimpleNamespace(bot=SimpleNamespace(add_view=lambda _: None))
        asyncio.run(bs.BueroSystemCog.cog_load(cog))
        self.assertIsNone(self.row()["claimed_by"])
        self.click(self.colleague)  # self.view is the stateless startup view
        self.assertEqual(self.waiting.moves, [OFFICE])

    def test_claim_expires_after_ttl(self):
        past = time.time() - bs.CLAIM_TTL_SECONDS - 1
        self.assertEqual(bs.claim_pending_request(self.message.id, GUILD, 9, now=past), "ok")
        self.assertFalse(bs.claim_is_active(self.row()))
        self.assertEqual(bs.claim_pending_request(self.message.id, GUILD, 8), "ok")

    def test_database_error_during_claim_moves_nobody(self):
        with patch.object(bs, "claim_pending_request", side_effect=sqlite3.OperationalError("locked")):
            result = self.click(self.colleague)
        self.assertIn("nicht gespeichert", result.texts[0])
        self.assertEqual(self.waiting.moves, [])
        self.assertIsNotNone(self.row())

    def test_database_error_while_reading_keeps_the_ping(self):
        with patch.object(bs, "get_pending_request_by_message", side_effect=sqlite3.OperationalError("locked")):
            result = self.click(self.colleague)
        self.assertIn("nicht geprüft", result.texts[0])
        self.assertEqual(self.message.deleted, 0)

    def test_database_error_during_release_still_answers(self):
        self.waiting.move_error = http_error(discord.Forbidden, 403)
        with patch.object(bs, "release_claim", side_effect=sqlite3.OperationalError("locked")):
            result = self.click(self.colleague)
        self.assertIn("bleibt offen", result.texts[-1])


class Migration(unittest.TestCase):
    def test_existing_rows_survive_new_columns(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = str(Path(tmp) / "old.db")
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE pending_requests (user_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL,
                ping_channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL, target_voice_id INTEGER NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP)""")
            conn.execute("INSERT INTO pending_requests VALUES (7, 1, 2, 3, 4, 'alt')")
            conn.commit()
            conn.close()
            with patch.object(bs, "DB_NAME", path):
                bs.init_db()
                bs.init_db()  # repeatable
                row = bs.get_pending_request(7)
                self.assertEqual((row["message_id"], row["created_at"], row["claimed_by"]), (3, "alt", None))
                self.assertEqual(bs.claim_pending_request(3, 1, 8), "ok")


if __name__ == "__main__":
    unittest.main()
