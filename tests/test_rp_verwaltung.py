"""K-02: RP panel – only authorised people may announce, nothing foreign is deleted, input is checked."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

import pulse_db
from cogs import rp_verwaltung as rp


class FakeMessage:
    def __init__(self, mid, channel):
        self.id, self.channel, self.deleted = mid, channel, False

    async def delete(self):
        self.deleted = True


class FakeChannel:
    def __init__(self, cid=555):
        self.id, self.mention, self.sent, self.messages, self.purged = cid, f"<#{cid}>", [], {}, False

    async def purge(self, **_):
        self.purged = True

    async def fetch_message(self, mid):
        if mid not in self.messages:
            raise discord.NotFound(SimpleNamespace(status=404, reason="x"), "gone")
        return self.messages[mid]

    async def send(self, content=None, allowed_mentions=None, **_):
        message = FakeMessage(900 + len(self.sent), self)
        self.messages[message.id] = message
        self.sent.append((content, allowed_mentions))
        return message


class FakeResponse:
    def __init__(self):
        self.done, self.messages, self.modal = False, [], None

    def is_done(self):
        return self.done

    async def defer(self, **_):
        self.done = True

    async def send_message(self, text, ephemeral=False):
        self.done = True
        self.messages.append((text, ephemeral))

    async def send_modal(self, modal):
        self.done = True
        self.modal = modal


class FakeInteraction:
    def __init__(self, channel, member):
        self.guild = SimpleNamespace(id=1, get_channel=lambda cid: channel if cid == channel.id else None)
        self.channel = FakeChannel(cid=777)  # where the panel lives – must never be used as fallback
        self.user = member
        self.response = FakeResponse()
        self.followup = SimpleNamespace(send=self._followup)

    async def _followup(self, text, ephemeral=False):
        self.response.messages.append((text, ephemeral))


def member(rid=10, admin=False):
    return SimpleNamespace(id=42, display_name="Max", roles=[SimpleNamespace(id=rid)],
                           guild_permissions=SimpleNamespace(administrator=admin))


class RPTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(rp, "DB_NAME", str(Path(self.tmp.name) / "rp.db")),
                        patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
                        patch("builtins.print")]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        rp._LAST_ANNOUNCEMENT.clear()
        self.channel = FakeChannel()
        rp.set_config("announcement_channel_id", self.channel.id)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def select(self, interaction, value="rp_start", perms=None):
        select = rp.RPSelect()
        select._values = [value]
        with patch.object(rp, "_member_perms", return_value=perms or {}), \
                patch.object(rp, "_audit", lambda *a, **k: None):
            asyncio.run(select.callback(interaction))
        return interaction

    def test_unauthorised_user_is_rejected_and_nothing_happens(self):
        interaction = self.select(FakeInteraction(self.channel, member()))
        self.assertEqual(self.channel.sent, [])
        self.assertFalse(self.channel.purged)
        self.assertTrue(interaction.response.messages[0][1])  # ephemeral
        self.assertIn("nicht berechtigt", interaction.response.messages[0][0])

    def test_leader_announces_with_everyone_only_and_no_purge(self):
        interaction = self.select(FakeInteraction(self.channel, member()), perms={"can_promote": True})
        content, mentions = self.channel.sent[0]
        self.assertIn("@everyone", content)
        self.assertTrue(mentions.everyone)
        self.assertFalse(mentions.roles)
        self.assertFalse(mentions.users)
        self.assertFalse(self.channel.purged)
        self.assertEqual(interaction.channel.sent, [])

    def test_only_own_previous_announcement_is_deleted_and_cooldown(self):
        self.select(FakeInteraction(self.channel, member()), perms={"is_admin": True})
        first = self.channel.messages[900]
        rp._LAST_ANNOUNCEMENT.clear()  # skip cooldown for the second announcement
        self.select(FakeInteraction(self.channel, member()), perms={"is_admin": True})
        self.assertTrue(first.deleted)
        self.assertEqual(len(self.channel.sent), 2)
        interaction = self.select(FakeInteraction(self.channel, member()), perms={"is_admin": True})
        self.assertEqual(len(self.channel.sent), 2)  # cooldown
        self.assertIn("Bitte warte", interaction.response.messages[-1][0])

    def test_send_failure_answers_and_releases_cooldown(self):
        async def forbidden(**_):
            raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no access")
        self.channel.send = forbidden
        interaction = self.select(FakeInteraction(self.channel, member()), perms={"is_admin": True})
        self.assertIn("nicht senden", interaction.response.messages[-1][0])
        self.assertEqual(rp._LAST_ANNOUNCEMENT, {})

    def test_stop_right_after_start_is_allowed(self):
        self.select(FakeInteraction(self.channel, member()), perms={"is_admin": True})
        modal = rp.RPStopModal()
        modal.uhrzeit._value = "18:00"
        interaction = FakeInteraction(self.channel, member())
        with patch.object(rp, "_member_perms", return_value={"is_admin": True}), patch.object(rp, "_audit", lambda *a, **k: None):
            asyncio.run(modal.on_submit(interaction))
        self.assertEqual(len(self.channel.sent), 2)
        self.assertIn("18:00 Uhr", self.channel.sent[1][0])

    def test_configured_extra_role_may_use_panel(self):
        pulse_db.set_setting(rp.ROLE_SETTING_KEY, [10])
        self.select(FakeInteraction(self.channel, member(rid=10)))
        self.assertEqual(len(self.channel.sent), 1)

    def test_missing_channel_gives_clear_error_instead_of_fallback(self):
        rp.set_config("announcement_channel_id", 0)
        interaction = self.select(FakeInteraction(self.channel, member()), perms={"is_admin": True})
        self.assertEqual(interaction.channel.sent, [])
        self.assertIn("Ankündigungskanal", interaction.response.messages[-1][0])

    def test_stop_time_is_validated_and_neutralised(self):
        self.assertEqual(rp.normalize_rp_time("14:30"), "14:30 Uhr")
        self.assertEqual(rp.normalize_rp_time(" 9.05 uhr "), "09:05 Uhr")
        self.assertEqual(rp.normalize_rp_time("offen"), "Offen")
        self.assertIsNone(rp.normalize_rp_time("@everyone kommt alle"))
        self.assertIsNone(rp.normalize_rp_time("25:99"))

    def test_modal_rejects_invalid_time_and_unauthorised(self):
        modal = rp.RPStopModal()
        modal.uhrzeit._value = "<@&1> jetzt"
        interaction = FakeInteraction(self.channel, member())
        with patch.object(rp, "_member_perms", return_value={"is_admin": True}), patch.object(rp, "_audit", lambda *a, **k: None):
            asyncio.run(modal.on_submit(interaction))
        self.assertEqual(self.channel.sent, [])
        self.assertIn("Uhrzeit", interaction.response.messages[-1][0])
        modal.uhrzeit._value = "18:00"
        interaction = FakeInteraction(self.channel, member())
        with patch.object(rp, "_member_perms", return_value={}), patch.object(rp, "_audit", lambda *a, **k: None):
            asyncio.run(modal.on_submit(interaction))
        self.assertEqual(self.channel.sent, [])


if __name__ == "__main__":
    unittest.main()
