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
        self.closed, self.events, self.event_kwargs = [], [], []
        self.patches = [
            patch.object(tickets, "close_ticket", lambda *a: self.closed.append(a) or True),
            patch.object(tickets, "record_event", lambda *a, **k: (self.events.append(a[0]), self.event_kwargs.append(k))),
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
        self.assertEqual(self.event_kwargs[0]["correlation_id"], f"ticket:{TICKET['id']}")  # F7: one ID across bot and dashboard
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

    def test_ticket_attachment_ceiling_and_private_file_permissions(self):
        channel = FakeChannel()
        attachments = [FakeAttachment(f"beweis-{i}.txt", 100) for i in range(pulse_transcripts.MAX_TICKET_ATTACHMENTS + 1)]
        async def history(**_):
            author = SimpleNamespace(id=7, __str__=lambda s: "max")
            yield SimpleNamespace(id=99, created_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
                                  content="Viele Anhänge", author=author, embeds=[], components=[], attachments=attachments)
        channel.history = history
        base = os.path.join(self.tmp.name, "private-data")
        text, saved = asyncio.run(pulse_transcripts.build_transcript(channel, "T-CAP", base_dir=base))
        self.assertEqual(saved, pulse_transcripts.MAX_TICKET_ATTACHMENTS)
        self.assertIn("Ticketlimit", text)
        files_dir = os.path.join(base, "transcripts", "T-CAP_anhaenge")
        self.assertEqual(len(os.listdir(files_dir)), pulse_transcripts.MAX_TICKET_ATTACHMENTS)
        if os.name == "posix":
            self.assertEqual(os.stat(files_dir).st_mode & 0o777, 0o700)
            first = os.path.join(files_dir, os.listdir(files_dir)[0])
            self.assertEqual(os.stat(first).st_mode & 0o777, 0o600)

    def test_transcript_message_limit_fails_without_returning_partial_archive(self):
        channel = FakeChannel()
        async def history(**_):
            author = SimpleNamespace(id=7, __str__=lambda s: "max")
            for i in range(2):
                yield SimpleNamespace(id=i, created_at=datetime(2026, 10, 20, 12, 0, tzinfo=timezone.utc),
                                      content="Nachricht", author=author, embeds=[], components=[], attachments=[])
        channel.history = history
        with patch.object(pulse_transcripts, "MAX_TRANSCRIPT_MESSAGES", 1):
            with self.assertRaises(pulse_transcripts.TranscriptError):
                asyncio.run(pulse_transcripts.build_transcript(channel, "T-LIMIT", base_dir=self.tmp.name))

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
