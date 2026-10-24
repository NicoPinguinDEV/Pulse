"""BOT-02: moderation log – routing per category, ignore lists, batching, voice throttle, new events."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord
from fastapi.testclient import TestClient

import pulse_db
import pulse_logcfg as lc
import webserver as ws
from tests.fakes import FakeChannel, FakeMember, FakeRole
from tests.test_pulse_admin_area import ADMIN, fake_auth_for


class ConfigTests(unittest.TestCase):
    def test_normalize_and_route(self):
        cfg = lc.normalize({"channels": {"voice": "5", "bogus": "7", "messages": "x"}, "ignore_roles": ["3", "y"], "voice": True})
        self.assertEqual(cfg["channels"], {"voice": 5})
        self.assertEqual(cfg["ignore_roles"], [3])
        self.assertEqual(lc.route("voice", cfg, 9), 5)
        self.assertEqual(lc.route("messages", cfg, 9), 9)               # falls back to the K-12 log channel
        self.assertEqual(lc.route("voice", {**cfg, "voice": False}, 9), 0)
        self.assertEqual(lc.route("members", lc.normalize({}), 0), 0)   # nothing configured → off

    def test_ignore_lists(self):
        cfg = lc.normalize({"ignore_channels": [1], "ignore_roles": [2]})
        self.assertTrue(lc.ignored(cfg, channel_id=1))
        self.assertTrue(lc.ignored(cfg, role_ids=[2, 3]))
        self.assertTrue(lc.ignored(cfg, is_bot=True))
        self.assertFalse(lc.ignored(lc.normalize({"ignore_bots": False}), is_bot=True))
        self.assertFalse(lc.ignored(cfg, channel_id=5, role_ids=[3]))

    def test_buffer_batches_and_is_bounded(self):
        buf = lc.LogBuffer()
        self.assertTrue(buf.add(1, "a"))
        self.assertFalse(buf.add(1, "b"))
        for n in range(lc.BUFFER_MAX + 20):
            buf.add(1, n)
        self.assertEqual(buf.pending(1), lc.BUFFER_MAX)
        self.assertEqual(len(buf.take(1)), lc.BATCH_MAX)

    def test_throttle(self):
        t = lc.Throttle(10)
        self.assertTrue(t.allow(7, 0))
        self.assertFalse(t.allow(7, 5))
        self.assertTrue(t.allow(7, 11))


class CogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        lc._CACHE.update(at=-1e9, cfg=None)
        self.log = FakeChannel(10, "log")
        self.voice_log = FakeChannel(11, "voice-log")
        channels = {10: self.log, 11: self.voice_log}
        self.guild = SimpleNamespace(id=1, get_channel=channels.get)
        pulse_db.set_setting("moderation_log_channel_id", "10")
        from cogs.advanced_logs import AdvancedLogs
        self.cog = AdvancedLogs(SimpleNamespace(get_guild=lambda gid: self.guild))

    def tearDown(self):
        lc._CACHE.update(at=-1e9, cfg=None)
        self.db.stop()
        self.tmp.cleanup()

    def run_and_flush(self, coro):
        async def go():
            with patch.object(lc, "BATCH_SECONDS", 0.01):
                await coro
                await asyncio.sleep(0.05)
        asyncio.run(go())

    def test_deleted_message_with_attachment_names(self):
        author = FakeMember(5, "Max")
        msg = SimpleNamespace(author=author, content="geheim", attachments=[SimpleNamespace(filename="beweis.png")])
        payload = SimpleNamespace(guild_id=1, channel_id=77, cached_message=msg)
        captured = []

        async def capture(channel, embed=None, **kw):
            captured.append(embed)
        with patch("cogs.advanced_logs.send_styled", capture):
            self.run_and_flush(self.cog.on_raw_message_delete(payload))
        fields = {f.name: f.value for f in captured[0].fields}
        self.assertEqual(fields["Anhänge"], "beweis.png")
        self.assertEqual(fields["Inhalt"], "geheim")

    def test_voice_goes_to_its_own_channel_and_is_throttled(self):
        lc.save({"channels": {"voice": 11}})
        member = FakeMember(5, "Max")
        a, b = SimpleNamespace(name="A"), SimpleNamespace(name="B")
        member.guild = self.guild

        async def two_moves():
            await self.cog.on_voice_state_update(member, SimpleNamespace(channel=None), SimpleNamespace(channel=a))
            await self.cog.on_voice_state_update(member, SimpleNamespace(channel=a), SimpleNamespace(channel=b))
        self.run_and_flush(two_moves())
        self.assertEqual(len(self.voice_log.sent), 1)   # second move within 10 s is throttled
        self.assertEqual(self.log.sent, [])

    def test_ignored_role_is_not_logged(self):
        staff = FakeRole(3, "Staff")
        lc.save({"ignore_roles": [3]})
        member = FakeMember(5, "Max", roles=[staff])
        member.guild = self.guild
        before = SimpleNamespace(roles=[], nick=None)
        self.run_and_flush(self.cog.on_member_update(before, member))
        self.assertEqual(self.log.sent, [])

    def test_many_entries_are_sent_in_batches(self):
        async def burst():
            for n in range(12):
                await self.cog.log(self.guild, "channels", discord.Embed(title=f"E{n}"))
        self.run_and_flush(burst())
        self.assertEqual(len(self.log.sent), 2)          # 10 + 2 instead of 12 messages
        self.assertEqual(len(self.log.sent[0].kwargs["embeds"]), 10)

    def test_failed_batch_does_not_stall_the_channel(self):
        # review finding: after one failed send the channel was never flushed again until a restart
        calls = []

        async def flaky(*a, embeds=None, **k):
            calls.append(len(embeds or []))
            if len(calls) == 1:
                raise discord.HTTPException(SimpleNamespace(status=400, reason="x"), "bad")
        self.log.send = flaky

        async def go():
            with patch.object(lc, "BATCH_SECONDS", 0.01):
                for n in range(12):
                    await self.cog.log(self.guild, "channels", discord.Embed(title=f"E{n}"))
                await asyncio.sleep(0.05)
                for n in range(3):
                    await self.cog.log(self.guild, "channels", discord.Embed(title=f"N{n}"))
                await asyncio.sleep(0.05)
        asyncio.run(go())
        self.assertEqual(calls, [10, 2, 3])   # first batch dropped and reported, the rest still goes out

    def test_rate_limit_keeps_the_batch(self):
        calls = []

        async def limited(*a, embeds=None, **k):
            calls.append(len(embeds or []))
            if len(calls) == 1:
                raise discord.HTTPException(SimpleNamespace(status=429, reason="x"), "slow")
        self.log.send = limited

        async def go():
            with patch.object(lc, "BATCH_SECONDS", 0.01), patch.object(lc, "RETRY_SECONDS", 0.01):
                for n in range(3):
                    await self.cog.log(self.guild, "channels", discord.Embed(title=f"E{n}"))
                await asyncio.sleep(0.1)
        asyncio.run(go())
        self.assertEqual(calls, [3, 3])        # same batch again after the pause

    def test_batches_respect_the_6000_character_limit(self):
        buf = lc.LogBuffer()
        for n in range(10):
            buf.add(1, "x" * 2000)
        self.assertEqual(len(buf.take(1, max_chars=lc.MESSAGE_CHARS)), 2)
        big = lc.LogBuffer()
        big.add(1, "y" * 9000)
        self.assertEqual(len(big.take(1, max_chars=lc.MESSAGE_CHARS)), 1)   # a single entry always goes

    def test_audit_log_ban_with_actor(self):
        entry = SimpleNamespace(action=discord.AuditLogAction.ban, target=SimpleNamespace(mention="<@9>"),
                                user=SimpleNamespace(mention="<@1>"), reason="Spam", guild=self.guild, changes=None)
        self.run_and_flush(self.cog.on_audit_log_entry_create(entry))
        self.assertEqual(len(self.log.sent), 1)


class AdminFormTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(Path(self.tmp.name) / "audit_logs.json"))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        lc._CACHE.update(at=-1e9, cfg=None)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_save_only_server_channels(self):
        from tests.test_pulse_admin_area import CHANNEL
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            bad = client.post("/admin/discord/log-routing", data={"channel_voice": "123"}, follow_redirects=False)
            self.assertIn("t=err", bad.headers["location"])
            ok = client.post("/admin/discord/log-routing", data={"channel_voice": str(CHANNEL.id), "voice": "1"},
                             follow_redirects=False)
            self.assertIn("t=ok", ok.headers["location"])
        self.assertEqual(lc.load()["channels"], {"voice": CHANNEL.id})
        self.assertFalse(lc.load()["ignore_bots"])


if __name__ == "__main__":
    unittest.main()
