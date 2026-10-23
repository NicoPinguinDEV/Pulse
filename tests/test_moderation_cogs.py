"""Discord-cog behavior tests using local fakes only."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.automod import PulseAutoMod
from cogs.antinuke import PulseAntiNuke
import pulse_db
from tests.fakes import FakeMember


class ModerationCogTests(unittest.TestCase):
    def test_automod_deletes_and_warns_once_then_timeouts_a_repeat(self):
        async def scenario():
            member = FakeMember(123, "Testmitglied")
            member.guild_permissions.manage_messages = False
            member.timeout = AsyncMock()
            member.send = AsyncMock()
            guild = SimpleNamespace(id=1, owner_id=999, name="Testserver", get_channel=lambda _cid: None)
            channel = SimpleNamespace(id=456)
            cog = PulseAutoMod(SimpleNamespace())
            cog._config = lambda: {
                "enabled": True,
                "rules": ["keywords"],
                "blocked_terms": ["verbotenes wort"],
            }

            def message(message_id):
                return SimpleNamespace(
                    id=message_id,
                    guild=guild,
                    author=member,
                    webhook_id=None,
                    channel=channel,
                    clean_content="Dieses VERBOTENES WORT bitte nicht",
                    mentions=[],
                    role_mentions=[],
                    mention_everyone=False,
                    delete=AsyncMock(),
                )

            with patch("cogs.automod.discord.Member", FakeMember), patch("builtins.print"):
                first = message(1)
                await cog.on_message(first)
                self.assertTrue(first.delete.awaited)
                member.send.assert_awaited_once()
                member.timeout.assert_not_awaited()

                second = message(2)
                await cog.on_message(second)
                self.assertTrue(second.delete.awaited)
                member.timeout.assert_awaited_once()
                self.assertEqual(member.timeout.await_args.args[0].total_seconds(), 600)
                member.send.assert_awaited_once()

        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(pulse_db, "DB_PATH", str(Path(tmp) / "pulse.db")):
            pulse_db.init_db()
            asyncio.run(scenario())
            events = pulse_db.events(event_type="AutoMod-Maßnahme")

        self.assertEqual(len(events), 2)
        self.assertCountEqual([json.loads(event["payload_json"])["strike"] for event in events], [1, 2])
        self.assertTrue(all("content" not in event["payload_json"] for event in events))

    def test_automod_ignores_exempt_moderators(self):
        async def scenario():
            moderator = FakeMember(124, "Moderator")
            moderator.guild_permissions.manage_messages = True
            guild = SimpleNamespace(id=1, owner_id=999, get_channel=lambda _cid: None)
            message = SimpleNamespace(
                guild=guild,
                author=moderator,
                webhook_id=None,
                channel=SimpleNamespace(id=456),
                clean_content="verbotenes wort",
                mentions=[],
                role_mentions=[],
                mention_everyone=False,
                delete=AsyncMock(),
            )
            cog = PulseAutoMod(SimpleNamespace())
            cog._config = lambda: {"enabled": True, "rules": ["keywords"], "blocked_terms": ["verbotenes wort"]}

            with patch("cogs.automod.discord.Member", FakeMember):
                await cog.on_message(message)

            message.delete.assert_not_awaited()

        asyncio.run(scenario())

    def test_antinuke_ignores_benign_renames_but_checks_permission_changes(self):
        async def scenario():
            guild = SimpleNamespace(id=1)
            cog = PulseAntiNuke(SimpleNamespace(user=SimpleNamespace(id=999)))
            cog._handle_event = AsyncMock()
            role_before = SimpleNamespace(
                guild=guild,
                id=1,
                permissions={"manage_messages": True},
                position=2,
                name="Support",
            )
            role_renamed = SimpleNamespace(
                guild=guild,
                id=1,
                permissions={"manage_messages": True},
                position=2,
                name="Support Team",
            )
            role_escalated = SimpleNamespace(
                guild=guild,
                id=1,
                permissions={"administrator": True},
                position=2,
                name="Support Team",
            )
            channel_before = SimpleNamespace(guild=guild, id=2, overwrites={}, name="support")
            channel_renamed = SimpleNamespace(guild=guild, id=2, overwrites={}, name="help")
            channel_reconfigured = SimpleNamespace(guild=guild, id=2, overwrites={"@everyone": "deny"}, name="help")

            await cog.on_guild_role_update(role_before, role_renamed)
            await cog.on_guild_role_update(role_before, role_escalated)
            await cog.on_guild_channel_update(channel_before, channel_renamed)
            await cog.on_guild_channel_update(channel_renamed, channel_reconfigured)

            self.assertEqual(cog._handle_event.await_count, 2)
            self.assertEqual(cog._handle_event.await_args_list[0].args, (guild, "role_update", 1))
            self.assertEqual(cog._handle_event.await_args_list[1].args, (guild, "channel_update", 2))

        asyncio.run(scenario())
