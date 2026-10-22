"""Pure policy tests for the configurable AutoMod and anti-nuke rules."""
import asyncio
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from cogs.antinuke import PulseAntiNuke
from pulse_moderation import (
    antinuke_window_exceeded,
    classify_message,
    is_trusted_actor,
    valid_domain,
)


class AutoModPolicyTests(unittest.TestCase):
    def test_only_enabled_rules_are_applied(self):
        self.assertIsNone(classify_message("discord.gg/invite", rules=[]))
        self.assertIsNone(classify_message("discord.gg/invite", rules=["spam"]))
        self.assertEqual(classify_message("discord.gg/invite", rules=["invites"]), "invites")

    def test_mass_mentions_exceed_configured_threshold(self):
        self.assertIsNone(classify_message("hello", rules=["mentions"], mention_count=5, mention_limit=5))
        self.assertEqual(classify_message("hello", rules=["mentions"], mention_count=6, mention_limit=5), "mentions")

    def test_custom_terms_and_domains(self):
        self.assertEqual(classify_message("This is a blocked phrase", rules=["keywords"], blocked_terms=["blocked phrase"]),
                         "keywords")
        self.assertIsNone(classify_message("class is in session", rules=["keywords"], blocked_terms=["ass"]))
        self.assertEqual(classify_message("visit https://sub.example.com/path", rules=["domains"],
                                          blocked_domains=["example.com"]), "domains")
        self.assertIsNone(classify_message("visit https://safe.example/path", rules=["domains"],
                                           blocked_domains=["bad.example"]))

    def test_spam_requires_repetition_or_a_flood_threshold(self):
        self.assertIsNone(classify_message("hello", rules=["spam"], duplicate_count=2, message_count=5))
        self.assertEqual(classify_message("hello", rules=["spam"], duplicate_count=3), "spam")
        self.assertEqual(classify_message("hello", rules=["spam"], message_count=6), "spam")

    def test_domain_validation_rejects_urls_and_invalid_hostnames(self):
        self.assertTrue(valid_domain("sub.example.com"))
        self.assertTrue(valid_domain("www.example.com"))
        self.assertFalse(valid_domain("https://example.com"))
        self.assertFalse(valid_domain("*.example.com"))


class AntiNukePolicyTests(unittest.TestCase):
    def test_server_owner_bot_and_explicit_trusted_roles_are_exempt(self):
        self.assertTrue(is_trusted_actor(10, owner_id=10, bot_id=20))
        self.assertTrue(is_trusted_actor(20, owner_id=10, bot_id=20))
        self.assertTrue(is_trusted_actor(30, owner_id=10, bot_id=20, role_ids=[8], trusted_role_ids=[8]))
        self.assertFalse(is_trusted_actor(30, owner_id=10, bot_id=20, role_ids=[9], trusted_role_ids=[8]))

    def test_only_actions_inside_the_window_reach_the_threshold(self):
        events = deque()
        self.assertFalse(antinuke_window_exceeded(events, 0, limit=3, window_seconds=10))
        self.assertFalse(antinuke_window_exceeded(events, 5, limit=3, window_seconds=10))
        self.assertTrue(antinuke_window_exceeded(events, 9, limit=3, window_seconds=10))
        self.assertFalse(antinuke_window_exceeded(events, 20, limit=3, window_seconds=10))

    def test_threshold_and_window_are_bounded(self):
        events = deque()
        self.assertFalse(antinuke_window_exceeded(events, 1, limit=1, window_seconds=1000))
        self.assertTrue(antinuke_window_exceeded(events, 2, limit=1, window_seconds=1000))

    def test_cog_bans_actor_only_after_distinct_audit_entries_reach_threshold(self):
        async def scenario():
            actor = SimpleNamespace(id=30)
            target = SimpleNamespace(id=99)
            entries = deque(
                SimpleNamespace(id=entry_id, user=actor, target=target)
                for entry_id in (1, 1, 2, 3)
            )
            guild = SimpleNamespace(
                id=7,
                owner_id=10,
                me=SimpleNamespace(
                    guild_permissions=SimpleNamespace(view_audit_log=True, ban_members=True),
                    top_role=10,
                ),
                get_member=lambda user_id: SimpleNamespace(top_role=5) if user_id == actor.id else None,
                ban=AsyncMock(),
            )

            async def audit_logs(**_kwargs):
                yield entries.popleft()

            guild.audit_logs = audit_logs
            bot = SimpleNamespace(user=SimpleNamespace(id=20))
            cog = PulseAntiNuke(bot)
            cog._config = lambda: {
                "enabled": True,
                "events": ["ban"],
                "action_limit": 3,
                "window_seconds": 10,
            }

            with patch("builtins.print"):
                await cog._handle_event(guild, "ban", target.id)
                await cog._handle_event(guild, "ban", target.id)
                await cog._handle_event(guild, "ban", target.id)
                guild.ban.assert_not_awaited()
                await cog._handle_event(guild, "ban", target.id)

            guild.ban.assert_awaited_once_with(
                actor,
                reason="Pulse Anti-Nuke: 3 destruktive Serveraktionen innerhalb von 10 Sekunden",
                delete_message_seconds=0,
            )

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
