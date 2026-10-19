import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import json

import discord

import pulse_embed_design

import pulse_welcome as w
from cogs import welcome as cog_module


def member(**overrides):
    data = {
        "id": 42, "mention": "<@42>", "display_name": "Max", "username": "max", "bot": False,
        "avatar_url": "https://cdn.discordapp.com/avatars/42/a.png",
        "created_at": datetime(2024, 1, 2, tzinfo=timezone.utc), "joined_at": datetime(2026, 10, 19, 12, tzinfo=timezone.utc),
        "guild_name": "© BOCHUM RP | V1", "member_count": 1234,
    }
    data.update(overrides)
    return data


def settings(**kinds):
    raw = {"welcome": {"enabled": True, "channel_id": "111111111111111111"},
           "leave": {"enabled": True, "channel_id": "222222222222222222"}}
    for kind, values in kinds.items():
        if kind == "include_bots":
            raw["include_bots"] = values
        else:
            raw[kind].update(values)
    return w.normalize_settings(raw)


class SettingsTests(unittest.TestCase):
    def test_defaults_are_backwards_compatible(self):
        s = w.normalize_settings(None)
        self.assertFalse(s["welcome"]["enabled"])
        self.assertFalse(s["leave"]["enabled"])
        self.assertFalse(s["include_bots"])
        self.assertTrue(s["welcome"]["mention"])
        self.assertEqual(w.validate_settings(s), [])

    def test_invalid_values_are_cleaned(self):
        s = w.normalize_settings({"welcome": {"channel_id": "abc", "banner_url": "http://x/y.png",
                                              "banner_file": "../../etc/passwd"}})
        self.assertEqual(s["welcome"]["channel_id"], "")
        self.assertEqual(s["welcome"]["banner_url"], "")
        self.assertEqual(s["welcome"]["banner_file"], "")

    def test_unknown_placeholder_and_missing_channel_block_saving(self):
        s = w.normalize_settings({"welcome": {"enabled": True, "message": "Hi {vorname}"}})
        problems = w.validate_settings(s)
        self.assertTrue(any("{vorname}" in p for p in problems))
        self.assertTrue(any("Zielkanal" in p for p in problems))


class MessageTests(unittest.TestCase):
    def test_welcome_avatar_thumbnail_and_url_banner(self):
        s = settings(welcome={"banner_url": "https://img.example/banner.png"})
        payload = w.build_member_message("welcome", s, member())
        self.assertEqual(payload["channel_id"], "111111111111111111")
        self.assertEqual(payload["spec"]["thumbnail_url"], "https://cdn.discordapp.com/avatars/42/a.png")
        self.assertEqual(payload["spec"]["image_url"], "https://img.example/banner.png")
        self.assertEqual(payload["content"], "<@42>")
        self.assertEqual(payload["mention_user_id"], 42)
        self.assertIn("<@42>", payload["spec"]["description"])
        self.assertIn("1.234", payload["spec"]["description"])
        self.assertNotIn("{", payload["spec"]["description"] + payload["spec"]["title"])

    def test_uploaded_banner_is_sent_as_attachment(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / w.BANNER_DIR
            folder.mkdir(parents=True)
            (folder / "leave_banner_0a1b2c3d.png").write_bytes(b"\x89PNG\r\n\x1a\n")
            s = settings(leave={"banner_file": "leave_banner_0a1b2c3d.png", "banner_url": "https://x.example/y.png"})
            payload = w.build_member_message("leave", s, member(), base_dir=tmp)
        self.assertEqual(payload["spec"]["image_url"], "attachment://leave_banner_0a1b2c3d.png")
        self.assertTrue(payload["banner_file"].endswith("leave_banner_0a1b2c3d.png"))

    def test_missing_banner_file_falls_back_cleanly(self):
        s = settings(welcome={"banner_file": "welcome_banner_0a1b2c3d.png"})
        payload = w.build_member_message("welcome", s, member(), base_dir=tempfile.gettempdir() + "/does-not-exist")
        self.assertEqual(payload["spec"]["image_url"], "")
        self.assertIsNone(payload["banner_file"])

    def test_disabled_and_bots(self):
        self.assertIsNone(w.build_member_message("welcome", settings(welcome={"enabled": False}), member()))
        self.assertIsNone(w.build_member_message("leave", settings(), member(bot=True)))
        self.assertIsNotNone(w.build_member_message("leave", settings(include_bots=True), member(bot=True)))
        self.assertIsNotNone(w.build_member_message("welcome", settings(welcome={"enabled": False}), member(), force=True))

    def test_leave_never_pings_and_handles_missing_data(self):
        s = settings(leave={"message": "{user} {member_count} {joined_at} {account_created}"})
        payload = w.build_member_message("leave", s, member(member_count=None, joined_at=None, created_at=None, avatar_url=""))
        self.assertEqual(payload["content"], "")
        self.assertIsNone(payload["mention_user_id"])
        self.assertEqual(payload["spec"]["description"], "**Max** – – –")
        self.assertEqual(payload["spec"]["thumbnail_url"], "")

    def test_mention_can_be_disabled_and_names_cannot_mass_ping(self):
        s = settings(welcome={"mention": False, "message": "Hallo {user} @everyone"})
        payload = w.build_member_message("welcome", s, member(display_name="@here Troll"))
        self.assertEqual(payload["content"], "")
        self.assertNotIn("@everyone", payload["spec"]["description"])
        self.assertNotIn("@here", payload["spec"]["description"])
        self.assertNotIn("<@42>", payload["spec"]["title"])

    def test_optional_first_steps_only_when_configured(self):
        self.assertEqual(w.build_member_message("welcome", settings(), member())["spec"]["fields"], [])
        payload = w.build_member_message("welcome", settings(welcome={"rules_channel_id": "333333333333333333"}), member())
        self.assertIn("<#333333333333333333>", payload["spec"]["fields"][0][1])


class FakeChannel:
    def __init__(self, perms=None, error=None):
        self.name = "willkommen"
        self.sent = []
        self._perms = perms or SimpleNamespace(send_messages=True, embed_links=True, attach_files=True)
        self._error = error

    def permissions_for(self, _):
        return self._perms

    async def send(self, **kwargs):
        if self._error:
            raise self._error
        self.sent.append(kwargs)


class FakeGuild:
    def __init__(self, channel):
        self.id = 1
        self.name = "© BOCHUM RP | V1"
        self.member_count = 10
        self.me = object()
        self._channel = channel

    def get_channel(self, cid):
        return self._channel if cid == 111111111111111111 else None


def fake_member(guild, bot=False):
    return SimpleNamespace(id=42, mention="<@42>", display_name="Max", name="max", bot=bot, display_avatar=None,
                           created_at=None, joined_at=None, guild=guild)


class CogTests(unittest.TestCase):
    def run_handle(self, channel, kind="welcome", raw=None, times=1, bot_member=False):
        cog = cog_module.WelcomeLeave(bot=None)
        cog.base_dir = tempfile.gettempdir()
        guild = FakeGuild(channel)
        raw = raw if raw is not None else {"welcome": {"enabled": True, "channel_id": "111111111111111111"},
                                           "leave": {"enabled": True, "channel_id": "111111111111111111"}}
        with patch("pulse_db.get_setting", return_value=raw):
            for _ in range(times):
                asyncio.run(cog._handle(kind, fake_member(guild, bot_member)))
        return cog

    def test_exactly_one_message_even_on_repeated_events(self):
        channel = FakeChannel()
        self.run_handle(channel, times=3)
        self.assertEqual(len(channel.sent), 1)
        sent = channel.sent[0]
        # Modern style (default): Components V2 container, ping inside the text, no embed/content.
        self.assertNotIn("embed", sent)
        self.assertNotIn("content", sent)
        layout = json.dumps(sent["view"].to_components())
        self.assertIn("<@42>", layout)
        self.assertEqual(json.loads(layout)[0]["type"], 17)  # container
        self.assertFalse(sent["allowed_mentions"].everyone)
        self.assertFalse(sent["allowed_mentions"].roles)
        self.assertEqual([u.id for u in sent["allowed_mentions"].users], [42])

    def test_classic_style_still_sends_embed(self):
        channel = FakeChannel()
        classic = pulse_embed_design.normalize_branding({"style": "classic"})
        with patch.object(pulse_embed_design, "branding", return_value=classic):
            self.run_handle(channel)
        self.assertEqual(channel.sent[0]["content"], "<@42>")
        self.assertIsNotNone(channel.sent[0]["embed"])

    def test_leave_sends_without_ping(self):
        channel = FakeChannel()
        self.run_handle(channel, kind="leave")
        self.assertNotIn("content", channel.sent[0])
        self.assertFalse(channel.sent[0]["allowed_mentions"].users)

    def test_disabled_or_bot_sends_nothing(self):
        channel = FakeChannel()
        self.run_handle(channel, raw={})
        self.run_handle(channel, bot_member=True)
        self.assertEqual(channel.sent, [])

    def test_missing_permissions_and_channel_do_not_raise(self):
        channel = FakeChannel(perms=SimpleNamespace(send_messages=False, embed_links=True, attach_files=True))
        with patch("builtins.print") as printed:
            self.run_handle(channel)
        self.assertEqual(channel.sent, [])
        self.assertIn("Nachrichten senden", printed.call_args[0][0])
        with patch("builtins.print") as printed:
            self.run_handle(None)
        self.assertIn("Zielkanal nicht gefunden", printed.call_args[0][0])

    def test_discord_errors_are_caught_without_retry(self):
        response = SimpleNamespace(status=403, reason="Forbidden")
        channel = FakeChannel(error=discord.Forbidden(response, "Missing Access"))
        with patch("builtins.print") as printed:
            self.run_handle(channel)
        self.assertIn("verweigert", printed.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
