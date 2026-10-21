"""BOT-08: raid/spam monitoring is opt-in, bounded and alert-only."""
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

from cogs.raid_watch import RaidWatch
from tests.fakes import FakeChannel, FakeGuild, FakeMember


class RaidWatchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "PULSE_RAID_WATCH_ENABLED": "true",
            "PULSE_RAID_WATCH_ALERT_CHANNEL_ID": "999",
            "PULSE_RAID_WATCH_JOIN_WINDOW_SECONDS": "120",
            "PULSE_RAID_WATCH_JOIN_THRESHOLD": "5",
            "PULSE_RAID_WATCH_NEW_ACCOUNT_THRESHOLD": "3",
            "PULSE_RAID_WATCH_MENTION_THRESHOLD": "4",
            "PULSE_RAID_WATCH_DUPLICATE_CHANNELS": "3",
        }, clear=False)
        self.env.start()
        self.guild = FakeGuild(gid=10)
        self.alert_channel = FakeChannel(999, "pulse-alerts")
        self.alert_channel.guild = self.guild
        self.bot = SimpleNamespace(get_channel=lambda cid: self.alert_channel if cid == 999 else None)
        self.cog = RaidWatch(self.bot)
        self.clock = [100.0]
        self.now_patch = patch.object(self.cog, "_now", side_effect=lambda: self.clock[0])
        self.now_patch.start()

    def tearDown(self):
        self.now_patch.stop()
        self.env.stop()

    def member(self, uid, *, age_days=1, bot=False):
        member = FakeMember(uid, f"Nutzer {uid}", bot=bot)
        member.guild = self.guild
        member.created_at = datetime.now(timezone.utc) - timedelta(days=age_days)
        return member

    def message(self, uid, channel_id, content, *, mentions=(), everyone=False, bot=False):
        channel = FakeChannel(channel_id, f"channel-{channel_id}")
        channel.guild = self.guild
        author = FakeMember(uid, f"Nutzer {uid}", bot=bot)
        return SimpleNamespace(
            guild=self.guild,
            channel=channel,
            author=author,
            content=content,
            mentions=list(mentions),
            role_mentions=[],
            mention_everyone=everyone,
            webhook_id=None,
        )

    async def test_disabled_by_default_never_sends_or_mutates_members(self):
        with patch.dict(os.environ, {"PULSE_RAID_WATCH_ENABLED": "false"}):
            cog = RaidWatch(self.bot)
        for uid in range(1, 8):
            await cog.on_member_join(self.member(uid))
        self.assertFalse(cog.enabled)
        self.assertEqual(self.alert_channel.sent, [])
        self.assertEqual(cog._joins, [])

    async def test_fresh_account_burst_generates_alert_without_action(self):
        for uid in range(1, 4):
            await self.cog.on_member_join(self.member(uid, age_days=2))
        self.assertEqual(len(self.alert_channel.sent), 1)
        message = self.alert_channel.sent[0]
        self.assertIn("Viele sehr junge Konten", message.content)
        self.assertIn("keine Sanktion", message.content)
        self.assertEqual(len(self.guild.members), 0)
        self.assertEqual(len(self.cog._joins), 3)

    async def test_old_account_join_burst_is_reported(self):
        for uid in range(1, 6):
            await self.cog.on_member_join(self.member(uid, age_days=365))
        self.assertEqual(len(self.alert_channel.sent), 1)
        self.assertIn("Viele Beitritte", self.alert_channel.sent[0].content)

    async def test_mass_mentions_are_alerted_with_mentions_disabled(self):
        message = self.message(1, 20, "Bitte beachtet diese Nachricht.", mentions=[object()] * 4)
        await self.cog.on_message(message)
        self.assertEqual(len(self.alert_channel.sent), 1)
        sent = self.alert_channel.sent[0]
        self.assertIn("Massen-Erwähnung", sent.content)
        self.assertFalse(sent.kwargs["allowed_mentions"].everyone)
        self.assertFalse(sent.kwargs["allowed_mentions"].roles)

    async def test_repeat_in_multiple_channels_is_detected_without_retaining_text(self):
        text = "Bitte lest die vollständige Nachricht und beachtet diese wiederholte Information." 
        for channel_id in (31, 32, 33):
            await self.cog.on_message(self.message(5, channel_id, text))
        self.assertEqual(len(self.alert_channel.sent), 1)
        self.assertIn("Identischer Text in mehreren Kanälen", self.alert_channel.sent[0].content)
        self.assertNotIn(text, repr(list(self.cog._messages)))
        self.assertEqual(len(self.cog._messages), 3)

    async def test_duplicate_alerts_are_cooled_down_per_guild_and_kind(self):
        text = "Wiederholte wichtige Nachricht über verschiedene Kanäle, bitte beachten."
        for round_no in range(2):
            for channel_id in (41 + round_no * 3, 42 + round_no * 3, 43 + round_no * 3):
                await self.cog.on_message(self.message(7, channel_id, text))
        self.assertEqual(len(self.alert_channel.sent), 1)

    async def test_unconfigured_alert_channel_fails_closed(self):
        with patch.dict(os.environ, {"PULSE_RAID_WATCH_ALERT_CHANNEL_ID": "", "PULSE_SYSTEM_ALERT_CHANNEL_ID": ""}):
            bot = SimpleNamespace(get_channel=lambda _cid: None)
            cog = RaidWatch(bot)
        self.assertFalse(await cog._alert(self.guild, "test", "Test", "Test", "Manuell prüfen"))
        self.assertEqual(self.alert_channel.sent, [])


if __name__ == "__main__":
    unittest.main()
