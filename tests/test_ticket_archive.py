"""K-06: a ticket channel is only deleted when the transcript was stored."""
import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import discord

import pulse_transcripts
from cogs import tickets


class FakeAttachment:
    def __init__(self, name, size):
        self.filename, self.size, self.url = name, size, f"https://cdn.example/{name}"

    async def save(self, path):
        with open(path, "wb") as fh:
            fh.write(b"x" * 4)


class FakeChannel:
    def __init__(self, broken=False, delete_error=None):
        self.broken, self.deleted, self.sent, self.edits, self.perms = broken, False, [], [], []
        self.delete_error = delete_error
        self.guild = SimpleNamespace(id=1, get_member=lambda uid: SimpleNamespace(id=uid) if uid else None)

    def history(self, **_):
        broken = self.broken

        async def gen():
            if broken:
                raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no history access")
            author = SimpleNamespace(id=7, __str__=lambda s: "max")
            yield SimpleNamespace(id=11, created_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc), content="Hallo",
                                  author=author, embeds=[], components=[],
                                  attachments=[FakeAttachment("beweis.png", 1000),
                                               FakeAttachment("video.mp4", pulse_transcripts.MAX_ATTACHMENT_BYTES + 1)])
        return gen()

    async def send(self, content=None, **kwargs):
        self.sent.append((content, kwargs))

    async def edit(self, **kwargs):
        self.edits.append(kwargs)

    async def set_permissions(self, target, **kwargs):
        self.perms.append((target, kwargs))

    async def delete(self, **_):
        if self.delete_error:
            raise self.delete_error
        self.deleted = True


TICKET = {"id": "T-42", "user_id": "7", "category": "support"}


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cwd = os.getcwd()
        os.chdir(self.tmp.name)
        self.closed, self.events = [], []
        self.patches = [
            patch.object(tickets, "close_ticket", lambda *a: self.closed.append(a) or True),
            patch.object(tickets, "record_event", lambda *a, **k: self.events.append(a[0])),
            patch.object(tickets, "drop_ticket_card", lambda *_: None),
            patch.object(tickets.asyncio, "sleep", self._no_sleep),
            patch("builtins.print"),
        ]
        for p in self.patches:
            p.start()

    async def _no_sleep(self, *_):
        return None

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def test_history_failure_keeps_channel_and_ticket_open(self):
        channel = FakeChannel(broken=True)
        ok = asyncio.run(tickets.archive_ticket(channel, dict(TICKET), 1, "Leitung", "erledigt"))
        self.assertFalse(ok)
        self.assertFalse(channel.deleted)
        self.assertEqual(self.closed, [])
        self.assertIn("ticket_archive_failed", self.events)
        self.assertTrue(channel.edits and channel.edits[0]["name"].startswith("geschlossen-"))
        self.assertFalse(channel.perms[0][1]["send_messages"])
        self.assertIn("nicht gelöscht", channel.sent[-1][0])

    def test_success_stores_transcript_and_small_attachments_then_deletes(self):
        channel = FakeChannel()
        ok = asyncio.run(tickets.archive_ticket(channel, dict(TICKET), 1, "Leitung", "erledigt"))
        self.assertTrue(ok)
        self.assertTrue(channel.deleted)
        self.assertEqual(self.closed[0][2], "T-42.txt")
        with open(os.path.join("transcripts", "T-42.txt"), encoding="utf-8") as fh:
            text = fh.read()
        self.assertIn("T-42_anhaenge/11_beweis.png (gesichert)", text)
        self.assertIn("video.mp4 (zu groß, nicht gesichert)", text)
        self.assertTrue(os.path.isfile(os.path.join("transcripts", "T-42_anhaenge", "11_beweis.png")))
        self.assertIn("file", channel.sent[0][1])

    def test_channel_delete_failure_is_reported_not_swallowed(self):
        import pulse_alerts
        pulse_alerts.reset()
        channel = FakeChannel(delete_error=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "missing perms"))
        ok = asyncio.run(tickets.archive_ticket(channel, dict(TICKET), 1, "Leitung", "erledigt"))
        self.assertTrue(ok)  # ticket closed and archived
        self.assertIn("ticket_channel_delete_failed", self.events)
        self.assertIn("nicht gelöscht", channel.sent[-1][0])
        self.assertEqual(len(pulse_alerts.pending()), 1)
        pulse_alerts.reset()

    def test_channel_already_gone_is_fine(self):
        channel = FakeChannel(delete_error=discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone"))
        self.assertTrue(asyncio.run(tickets.archive_ticket(channel, dict(TICKET), 1, "Leitung", "erledigt")))
        self.assertNotIn("ticket_channel_delete_failed", self.events)

    def test_transcript_is_also_uploaded_to_log_channel(self):
        channel, log = FakeChannel(), FakeChannel()
        name = asyncio.run(pulse_transcripts.archive_transcript(channel, "T-43", log_channel=log))
        self.assertEqual(name, "T-43.txt")
        self.assertEqual(len(log.sent), 1)
        self.assertIn("T-43", log.sent[0][0])


if __name__ == "__main__":
    unittest.main()
