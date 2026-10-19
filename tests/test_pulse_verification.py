"""Verification flow (B.1): join role, button, idempotency, failures, persistence, panel publish.

Discord objects are fakes – no real roles or members are changed.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import json

import discord

import pulse_embed_design

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

    async def delete(self):
        self.deleted = True


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
        layout = json.dumps(sent["view"].to_components())
        self.assertIn("attachment://verify_banner_0a1b2c3d.png", layout)
        self.assertIn("pulse:verify:v1", layout)
        asyncio.run(self.cog.publish_panel(guild))
        self.assertEqual([f.filename for f in channel.existing.last_edit["attachments"]], ["verify_banner_0a1b2c3d.png"])

    def test_without_upload_the_url_banner_is_used_and_no_file_sent(self):
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": True, "channel_id": "111111111111111111",
                                             "banner_url": "https://x.example/a.png"})
        channel = FakeChannel()
        asyncio.run(self.cog.publish_panel(FakeGuild(channel=channel)))
        self.assertEqual(channel.sent[0]["files"], [])
        self.assertIn("https://x.example/a.png", json.dumps(channel.sent[0]["view"].to_components()))

    def test_classic_style_uses_embed_and_style_switch_replaces_panel_once(self):
        classic = pulse_embed_design.normalize_branding({"style": "classic"})
        channel = FakeChannel()
        guild = FakeGuild(channel=channel)
        with patch.object(pulse_embed_design, "branding", return_value=classic), \
                patch.object(cog_module, "branding", return_value=classic):
            asyncio.run(self.cog.publish_panel(guild))
        self.assertIsNotNone(channel.sent[0]["embed"])
        old = channel.existing
        result = asyncio.run(self.cog.publish_panel(guild))  # default style is modern now
        self.assertEqual(result["action"], "veröffentlicht")
        self.assertTrue(getattr(old, "deleted", False))
        self.assertEqual(len(channel.sent), 2)
        self.assertIn("view", channel.sent[1])
        self.assertEqual(v.normalize_settings(pulse_db.get_setting(v.SETTING_KEY, {}))["panel_style"], "modern")
        asyncio.run(self.cog.publish_panel(guild))  # same style again: edit, no new message
        self.assertEqual(len(channel.sent), 2)

    def test_modern_persistent_layout_is_registered_after_restart(self):
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": True, "panel_style": "modern"})
        bot = FakeBot()
        with patch.object(cog_module, "_guild_id", return_value=GUILD_ID):
            cog_module.Verification(bot)
        view = bot.views[0]
        self.assertIsInstance(view, discord.ui.LayoutView)
        self.assertTrue(view.is_persistent())
        self.assertIn("pulse:verify:v1", json.dumps(view.to_components()))

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


class ReminderChannel:
    def __init__(self, delete_error=None):
        self.id = 111111111111111111
        self.name = "verifizierung"
        self.messages = {}
        self.sent = []
        self.deleted = []
        self.delete_error = delete_error

    def permissions_for(self, _):
        return SimpleNamespace(send_messages=True, mention_everyone=False)

    async def fetch_message(self, message_id):
        if message_id not in self.messages:
            raise http_error(discord.NotFound, 404)
        return self.messages[message_id]

    async def send(self, content=None, **kwargs):
        message_id = 800000000000000000 + len(self.sent)
        channel = self

        class Msg:
            id = message_id

            async def delete(self_inner):
                if channel.delete_error:
                    raise channel.delete_error
                channel.deleted.append(message_id)
                channel.messages.pop(message_id, None)

        self.messages[message_id] = Msg()
        self.sent.append((content, kwargs))
        return self.messages[message_id]


class ReminderTests(Base):
    def setUp(self):
        super().setUp()
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": True, "channel_id": "111111111111111111",
                                             "message_id": "900000000000000001", "published_channel_id": "111111111111111111"})
        self.channel = ReminderChannel()
        role = FakeRole(UNVERIFIED, 5)
        role.members = [object(), object()]
        role.mentionable = True
        self.role = role
        self.guild = FakeGuild(roles={UNVERIFIED: role, CITIZEN: FakeRole(CITIZEN, 6)}, channel=self.channel)

    def run_at(self, minute):
        from datetime import datetime, timedelta, timezone
        now = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc) + timedelta(minutes=minute)
        return asyncio.run(self.cog.run_reminder(self.guild, now=now))

    def test_pings_only_the_unverified_role_once_per_interval(self):
        self.assertEqual(self.run_at(0), "sent")
        content, kwargs = self.channel.sent[0]
        self.assertTrue(content.startswith(f"<@&{UNVERIFIED}>"))
        mentions = kwargs["allowed_mentions"]
        self.assertFalse(mentions.everyone)
        self.assertFalse(mentions.users)
        self.assertEqual([r.id for r in mentions.roles], [UNVERIFIED])
        self.assertEqual(self.run_at(30), "wait")
        self.assertEqual(len(self.channel.sent), 1)

    def test_only_one_reminder_stays_in_the_channel(self):
        self.run_at(0)
        self.run_at(61)
        self.run_at(122)
        self.assertEqual(len(self.channel.sent), 3)
        self.assertEqual(len(self.channel.messages), 1)
        self.assertEqual(len(self.channel.deleted), 2)

    def test_nobody_unverified_means_no_ping_and_cleanup(self):
        self.run_at(0)
        self.role.members = []
        self.assertEqual(self.run_at(61), "cleaned")
        self.assertEqual(self.channel.messages, {})
        self.assertEqual(self.run_at(200), "wait")
        self.assertEqual(len(self.channel.sent), 1)

    def test_off_when_disabled_or_panel_unpublished(self):
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": True, "channel_id": "111111111111111111", "reminder_enabled": False,
                                             "message_id": "900000000000000001"})
        self.assertEqual(self.run_at(0), "off")
        pulse_db.set_setting(v.SETTING_KEY, {"enabled": True, "channel_id": "111111111111111111"})
        self.assertEqual(self.run_at(0), "off")
        self.assertEqual(self.channel.sent, [])

    def test_restart_does_not_ping_again_early(self):
        self.run_at(0)
        restarted_bot = FakeBot()
        with patch.object(cog_module, "_guild_id", return_value=GUILD_ID):
            self.cog = cog_module.Verification(restarted_bot)
        self.assertEqual(self.run_at(10), "wait")
        self.assertEqual(len(self.channel.sent), 1)

    def test_text_cannot_add_mass_pings_and_interval_is_bounded(self):
        s = v.normalize_settings({"reminder_text": "@everyone <@&1> los", "reminder_minutes": "1"})
        self.assertNotIn("@everyone", s["reminder_text"])
        self.assertNotIn("<@&1>", s["reminder_text"])
        self.assertEqual(s["reminder_minutes"], v.REMINDER_MIN_MINUTES)
        self.assertEqual(v.normalize_settings({"reminder_minutes": "99999"})["reminder_minutes"], v.REMINDER_MAX_MINUTES)
        self.assertEqual(v.normalize_settings({"reminder_minutes": "abc"})["reminder_minutes"], 60)

    def test_not_mentionable_role_is_reported(self):
        self.role.mentionable = False
        self.run_at(0)
        self.assertIn("nicht erwähnbar", self.stored_errors()[-1]["text"])


if __name__ == "__main__":
    unittest.main()
