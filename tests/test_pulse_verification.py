"""Verification flow (B.1): join role, button, idempotency, failures, persistence, panel publish.

Discord objects are fakes – no real roles or members are changed.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

import pulse_db
import pulse_verification as v
from cogs import verification as cog_module

UNVERIFIED = v.UNVERIFIED_ROLE_ID
CITIZEN = v.CITIZEN_ROLE_ID
GUILD_ID = 1474514929351524616


def http_error(cls, status):
    return cls(SimpleNamespace(status=status, reason="x"), "error")


class FakeRole:
    def __init__(self, role_id, position):
        self.id = role_id
        self.position = position
        self.name = {UNVERIFIED: "Unverifiziert", CITIZEN: "Bürger"}.get(role_id, "Rolle")


class FakeMember:
    def __init__(self, guild, roles=(), bot=False, fail_add=None, fail_remove=None):
        self.guild = guild
        self.id = 4242
        self.roles = list(roles)
        self.bot = bot
        self.fail_add = fail_add
        self.fail_remove = fail_remove
        self.calls = []

    async def add_roles(self, *roles, reason=None):
        self.calls.append(("add", [r.id for r in roles]))
        if self.fail_add:
            raise self.fail_add
        self.roles.extend(roles)

    async def remove_roles(self, *roles, reason=None):
        self.calls.append(("remove", [r.id for r in roles]))
        if self.fail_remove:
            raise self.fail_remove
        self.roles = [r for r in self.roles if r not in roles]


class FakeUser:
    """A user outside the server context (no roles)."""
    id = 4242


class FakeGuild:
    def __init__(self, guild_id=GUILD_ID, roles=None, manage_roles=True, top=100, channel=None):
        self.id = guild_id
        self.icon = None
        self._roles = {UNVERIFIED: FakeRole(UNVERIFIED, 5), CITIZEN: FakeRole(CITIZEN, 6)} if roles is None else roles
        self.me = SimpleNamespace(guild_permissions=SimpleNamespace(manage_roles=manage_roles), top_role=SimpleNamespace(position=top))
        self._channel = channel

    def get_role(self, role_id):
        return self._roles.get(role_id)

    def get_channel(self, cid):
        return self._channel if self._channel and cid == self._channel.id else None


class FakeResponse:
    def __init__(self):
        self.done = False
        self.messages = []

    def is_done(self):
        return self.done

    async def send_message(self, text, ephemeral=False):
        self.done = True
        self.messages.append((text, ephemeral))

    async def defer(self, ephemeral=False, thinking=False):
        self.done = True


class FakeInteraction:
    def __init__(self, guild, user):
        self.guild = guild
        self.user = user
        self.response = FakeResponse()
        self.followup = SimpleNamespace(send=self._followup)
        self.sent = self.response.messages

    async def _followup(self, text, ephemeral=False):
        self.response.messages.append((text, ephemeral))


class FakeBot:
    def __init__(self):
        self.views = []

    def add_view(self, view, message_id=None):
        self.views.append(view)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": True, "channel_id": "111111111111111111"})
        self.bot = FakeBot()
        with patch.object(cog_module, "_guild_id", return_value=GUILD_ID):
            self.cog = cog_module.Verification(self.bot)
        self.quiet = patch("builtins.print")
        self.quiet.start()

    def tearDown(self):
        self.quiet.stop()
        self.db.stop()
        self.tmp.cleanup()

    def click(self, member, guild=None):
        interaction = FakeInteraction(guild or member.guild, member)
        status = asyncio.run(self.cog.handle_click(interaction))
        return status, interaction.sent

    def stored_errors(self):
        return v.normalize_settings(pulse_db.get_setting(v.SETTING_KEY, {}))["errors"]


class PlanTests(unittest.TestCase):
    def test_join_and_verify_plans(self):
        self.assertEqual(v.plan_join([], is_bot=False, enabled=True), "assign")
        self.assertEqual(v.plan_join([], is_bot=False, enabled=False), "disabled")
        self.assertEqual(v.plan_join([], is_bot=True, enabled=True), "bot")
        self.assertEqual(v.plan_join([CITIZEN], is_bot=False, enabled=True), "already_verified")
        self.assertEqual(v.plan_verify([UNVERIFIED]), {"add_citizen": True, "remove_unverified": True, "already_done": False})
        self.assertTrue(v.plan_verify([CITIZEN])["already_done"])
        self.assertEqual(v.plan_verify([CITIZEN, UNVERIFIED]), {"add_citizen": False, "remove_unverified": True, "already_done": False})

    def test_settings_defaults_and_validation(self):
        s = v.normalize_settings(None)
        self.assertFalse(s["enabled"])
        self.assertEqual(v.validate_settings(v.normalize_settings({"enabled": True})),
                         ["Bitte einen Verifikationskanal wählen, bevor du die Verifikation aktivierst."])
        self.assertTrue(v.validate_settings(s, "http://x/y.png"))
        errors = s
        for i in range(15):
            errors = v.add_error(errors, "2026-10-19T12:00:00", f"Fehler {i}")
        self.assertEqual(len(errors["errors"]), v.MAX_ERRORS)

    def test_panel_never_mass_pings(self):
        spec = v.build_panel_spec(v.normalize_settings({"description": "Hallo @everyone <@&1>"}))
        self.assertNotIn("@everyone", spec["description"])
        self.assertNotIn("<@&1>", spec["description"])


class JoinTests(Base):
    def join(self, member):
        asyncio.run(self.cog.on_member_join(member))

    def test_1_new_member_gets_unverified_role_once(self):
        member = FakeMember(FakeGuild())
        self.join(member)
        self.join(member)  # duplicate gateway event
        self.assertEqual(member.calls, [("add", [UNVERIFIED])])

    def test_bots_and_disabled_are_skipped(self):
        member = FakeMember(FakeGuild(), bot=True)
        self.join(member)
        self.assertEqual(member.calls, [])
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": False})
        other = FakeMember(FakeGuild())
        other.id = 1
        self.join(other)
        self.assertEqual(other.calls, [])

    def test_4_join_role_failure_is_recorded(self):
        member = FakeMember(FakeGuild(), fail_add=http_error(discord.Forbidden, 403))
        self.join(member)
        self.assertIn("verweigert", self.stored_errors()[-1]["text"])

    def test_6_missing_role_or_hierarchy_does_not_call_discord(self):
        member = FakeMember(FakeGuild(top=5))
        self.join(member)
        self.assertEqual(member.calls, [])
        self.assertIn("nicht unter der höchsten Pulse-Rolle", self.stored_errors()[-1]["text"])

    def test_wrong_guild_is_ignored(self):
        member = FakeMember(FakeGuild(guild_id=999))
        self.join(member)
        self.assertEqual(member.calls, [])


class ClickTests(Base):
    def test_2_success_adds_citizen_before_removing_unverified(self):
        guild = FakeGuild()
        member = FakeMember(guild, roles=[guild.get_role(UNVERIFIED)])
        status, sent = self.click(member)
        self.assertEqual(status, "verified")
        self.assertEqual(member.calls, [("add", [CITIZEN]), ("remove", [UNVERIFIED])])
        self.assertTrue(all(ephemeral for _, ephemeral in sent))
        self.assertIn("abgeschlossen", sent[-1][0])

    def test_3_double_click_and_already_verified_are_idempotent(self):
        guild = FakeGuild()
        member = FakeMember(guild, roles=[guild.get_role(UNVERIFIED)])
        self.click(member)
        status, sent = self.click(member)  # immediate second click
        self.assertEqual(status, "cooldown")
        self.cog._last_click.clear()
        status, sent = self.click(member)
        self.assertEqual(status, "already")
        self.assertEqual(member.calls, [("add", [CITIZEN]), ("remove", [UNVERIFIED])])

    def test_5_citizen_failure_keeps_unverified_role(self):
        guild = FakeGuild()
        member = FakeMember(guild, roles=[guild.get_role(UNVERIFIED)], fail_add=http_error(discord.HTTPException, 500))
        status, sent = self.click(member)
        self.assertEqual(status, "citizen_failed")
        self.assertEqual(member.calls, [("add", [CITIZEN])])
        self.assertIn(guild.get_role(UNVERIFIED), member.roles)
        self.assertFalse(sent[-1][0].startswith("✅"))
        self.assertIn("Unverifiziert-Rolle bleibt bestehen", self.stored_errors()[-1]["text"])

    def test_partial_failure_is_reported_honestly(self):
        guild = FakeGuild()
        member = FakeMember(guild, roles=[guild.get_role(UNVERIFIED)], fail_remove=http_error(discord.Forbidden, 403))
        status, sent = self.click(member)
        self.assertEqual(status, "partial")
        self.assertIn("Bürgerrolle erhalten", sent[-1][0])

    def test_6_missing_role_permission_or_wrong_guild(self):
        for guild in (FakeGuild(roles={UNVERIFIED: FakeRole(UNVERIFIED, 5)}), FakeGuild(manage_roles=False), FakeGuild(top=6)):
            self.cog._last_click.clear()
            member = FakeMember(guild, roles=[FakeRole(UNVERIFIED, 5)])
            status, sent = self.click(member)
            self.assertEqual(status, "misconfigured")
            self.assertEqual(member.calls, [])
            self.assertFalse(sent[-1][0].startswith("✅"))
        self.cog._last_click.clear()
        member = FakeMember(FakeGuild(guild_id=999))
        self.assertEqual(self.click(member)[0], "wrong_guild")
        self.assertEqual(member.calls, [])
        self.cog._last_click.clear()
        self.assertEqual(self.click(FakeUser(), guild=FakeGuild())[0], "wrong_guild")

    def test_disabled_button_does_nothing(self):
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": False})
        member = FakeMember(FakeGuild(), roles=[FakeRole(UNVERIFIED, 5)])
        self.assertEqual(self.click(member)[0], "disabled")
        self.assertEqual(member.calls, [])

    def test_7_persistent_button_survives_restart(self):
        view = self.bot.views[0]
        self.assertIsNone(view.timeout)
        button = view.children[0]
        self.assertEqual(button.custom_id, "pulse:verify:v1")
        # simulated restart: a brand-new cog registers the same custom_id again and handles clicks
        restarted_bot = FakeBot()
        with patch.object(cog_module, "_guild_id", return_value=GUILD_ID):
            restarted = cog_module.Verification(restarted_bot)
        self.assertEqual(restarted_bot.views[0].children[0].custom_id, "pulse:verify:v1")
        guild = FakeGuild()
        member = FakeMember(guild, roles=[guild.get_role(UNVERIFIED)])
        interaction = FakeInteraction(guild, member)
        asyncio.run(restarted_bot.views[0].children[0].callback(interaction))
        self.assertEqual(member.calls, [("add", [CITIZEN]), ("remove", [UNVERIFIED])])


class FakeMessage:
    def __init__(self, message_id):
        self.id = message_id
        self.edits = 0

    async def edit(self, **kwargs):
        self.edits += 1
        self.last_edit = kwargs


class FakeChannel:
    def __init__(self, existing=None, fetch_error=None):
        self.id = 111111111111111111
        self.name = "verifizierung"
        self.sent = []
        self.existing = existing
        self.fetch_error = fetch_error

    def permissions_for(self, _):
        return SimpleNamespace(send_messages=True, embed_links=True)

    async def fetch_message(self, message_id):
        if self.fetch_error:
            raise self.fetch_error
        if self.existing and self.existing.id == message_id:
            return self.existing
        raise http_error(discord.NotFound, 404)

    async def send(self, **kwargs):
        message = FakeMessage(900000000000000000 + len(self.sent))
        self.sent.append(kwargs)
        self.existing = message
        return message


class PublishTests(Base):
    def setUp(self):
        super().setUp()
        self.cog.base_dir = self.tmp.name

    def test_uploaded_banner_is_attached_on_send_and_edit(self):
        folder = Path(self.tmp.name) / "uploads" / "welcome_banners"
        folder.mkdir(parents=True)
        (folder / "verify_banner_0a1b2c3d.png").write_bytes(bytes([0x89]) + b"PNG\r\n\x1a\n")
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": True, "channel_id": "111111111111111111",
                                             "banner_file": "verify_banner_0a1b2c3d.png", "banner_url": "https://x.example/a.png"})
        channel = FakeChannel()
        guild = FakeGuild(channel=channel)
        asyncio.run(self.cog.publish_panel(guild))
        sent = channel.sent[0]
        self.assertEqual([f.filename for f in sent["files"]], ["verify_banner_0a1b2c3d.png"])
        self.assertEqual(sent["embed"].image.url, "attachment://verify_banner_0a1b2c3d.png")
        asyncio.run(self.cog.publish_panel(guild))
        self.assertEqual([f.filename for f in channel.existing.last_edit["attachments"]], ["verify_banner_0a1b2c3d.png"])

    def test_without_upload_the_url_banner_is_used_and_no_file_sent(self):
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": True, "channel_id": "111111111111111111",
                                             "banner_url": "https://x.example/a.png"})
        channel = FakeChannel()
        asyncio.run(self.cog.publish_panel(FakeGuild(channel=channel)))
        self.assertEqual(channel.sent[0]["files"], [])
        self.assertEqual(channel.sent[0]["embed"].image.url, "https://x.example/a.png")

    def test_unsafe_banner_file_name_is_ignored(self):
        s = v.normalize_settings({"banner_file": "../../pulse.db"})
        self.assertEqual(s["banner_file"], "")

    def test_8_publish_edits_existing_message_instead_of_duplicating(self):
        channel = FakeChannel()
        guild = FakeGuild(channel=channel)
        first = asyncio.run(self.cog.publish_panel(guild))
        second = asyncio.run(self.cog.publish_panel(guild))
        self.assertEqual(first["action"], "veröffentlicht")
        self.assertEqual(second["action"], "aktualisiert")
        self.assertEqual(len(channel.sent), 1)
        self.assertEqual(channel.existing.edits, 1)

    def test_deleted_message_is_recreated_once_and_errors_do_not_duplicate(self):
        channel = FakeChannel()
        guild = FakeGuild(channel=channel)
        asyncio.run(self.cog.publish_panel(guild))
        channel.existing = None  # someone deleted the panel
        asyncio.run(self.cog.publish_panel(guild))
        self.assertEqual(len(channel.sent), 2)
        channel.fetch_error = http_error(discord.HTTPException, 503)
        with self.assertRaises(ValueError):
            asyncio.run(self.cog.publish_panel(guild))
        self.assertEqual(len(channel.sent), 2)

    def test_publish_requires_channel(self):
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": False})
        with self.assertRaises(ValueError):
            asyncio.run(self.cog.publish_panel(FakeGuild(channel=FakeChannel())))


if __name__ == "__main__":
    unittest.main()
