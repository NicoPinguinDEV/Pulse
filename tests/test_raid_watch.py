"""BOT-08: raid/spam early warning – detection, cooldown, bounded memory, no automatic action."""
import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_raid as pr


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.w = pr.RaidWatch()

    def test_join_wave_and_new_accounts(self):
        alerts = [self.w.member_joined(100 + i, 400) for i in range(pr.JOIN_WAVE)]
        self.assertIsNone(alerts[0])
        self.assertEqual(alerts[-1]["kind"], "join_wave")
        self.assertIsNone(self.w.member_joined(130, 400))                # cooldown
        w = pr.RaidWatch()
        young = [w.member_joined(i * 30, 1) for i in range(pr.NEW_ACCOUNTS)]
        self.assertIn("sehr neue Konten", young[-1]["text"])
        normal = pr.RaidWatch()
        self.assertTrue(all(normal.member_joined(i * 30, 900) is None for i in range(pr.NEW_ACCOUNTS)))

    def test_mass_mentions_and_everyone_attempt(self):
        self.assertEqual(self.w.message(1, 7, 55, "hallo", pr.MASS_MENTIONS, False)["kind"], "mass_mention")
        other = pr.RaidWatch()
        self.assertIn("@everyone", other.message(1, 7, 55, "@everyone kommt alle", 0, True)["text"])
        self.assertIsNone(pr.RaidWatch().message(1, 7, 55, "normale Nachricht", 2, False))

    def test_copy_spam_across_channels(self):
        text = "Gratis Nitro hier: https://example.invalid/claim"
        self.assertIsNone(self.w.message(10, 7, 1, text, 0, False))
        self.assertIsNone(self.w.message(11, 7, 2, text.upper(), 0, False))
        alert = self.w.message(12, 7, 3, text, 0, False)
        self.assertEqual(alert["kind"], "copy_spam")
        self.assertEqual(alert["details"]["channels"], [1, 2, 3])
        w = pr.RaidWatch()
        for ch in range(1, 4):  # spread over more than a minute → no alert
            self.assertIsNone(w.message(ch * 40, 7, ch, text, 0, False))
        self.assertIsNone(pr.RaidWatch().message(1, 7, 1, "hi", 0, False))

    def test_memory_is_bounded(self):
        for n in range(pr.MAX_AUTHORS + 100):
            self.w.message(1, n, 1, "eine längere Nachricht zum Test", 0, False)
        self.assertLessEqual(len(self.w.recent), pr.MAX_AUTHORS)
        self.assertEqual(self.w.joins.maxlen, 500)

    def test_alert_text_pings_nobody_and_promises_no_action(self):
        alert = self.w.message(1, 7, 55, "x", pr.MASS_MENTIONS, False)
        text = pr.alert_text(alert)
        self.assertIn("<@7>", text)
        self.assertIn("nichts automatisch geändert", text)


class CogTests(unittest.TestCase):
    def test_alert_is_sent_without_pings_and_nothing_is_changed(self):
        from cogs import raid_watch
        sent = []

        class Channel:
            async def send(self, content, **kw):
                sent.append((content, kw))

        guild = SimpleNamespace(get_channel=lambda cid: Channel() if cid else None, members=[])
        cog = raid_watch.RaidWatchCog(SimpleNamespace())
        with patch.dict("os.environ", {"PULSE_SYSTEM_ALERT_CHANNEL_ID": "99"}), \
                patch("webserver.log_audit", lambda *a, **k: None), patch("webserver.load_config", lambda: {}):
            asyncio.run(cog.send_alert(guild, pr.RaidWatch().message(1, 7, 55, "x", pr.MASS_MENTIONS, False)))
        content, kw = sent[0]
        self.assertIn("Frühwarnung", content)
        mentions = kw["allowed_mentions"]
        self.assertFalse(mentions.everyone or mentions.roles or mentions.users)

    def test_no_moderation_calls_in_the_cog(self):
        source = (Path(pr.__file__).parent / "cogs" / "raid_watch.py").read_text(encoding="utf-8")
        for forbidden in (".ban(", ".kick(", ".timeout(", "set_permissions(", ".edit(", "remove_roles(", "add_roles("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
