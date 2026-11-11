"""Webhook-monitor metadata, secrecy and alert integration tests."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord

from cogs.antinuke import PulseAntiNuke
import pulse_webhook_audit


class WebhookAuditMetadataTests(unittest.TestCase):
    def test_metadata_contains_only_allowlisted_safe_fields(self):
        target = SimpleNamespace(
            id=901, name="support-hook", channel_id=77,
            channel=SimpleNamespace(id=77, name="support"),
            type=SimpleNamespace(name="incoming"),
            token="DO_NOT_STORE_TOKEN",
            url="https://discord.example/api/webhooks/901/SECRET",
        )
        entry = SimpleNamespace(
            id=12345, target=target,
            before=SimpleNamespace(name="old-hook", channel_id=70, token="OLD_SECRET", url="https://old.invalid"),
            after=SimpleNamespace(name="support-hook", channel_id=77, token="NEW_SECRET", url="https://new.invalid"),
        )
        payload = pulse_webhook_audit.audit_entry_metadata(
            entry, guild_id=7, actor_id=42, action="webhook_update"
        )
        self.assertEqual(payload["guild_id"], 7)
        self.assertEqual(payload["webhook_id"], 901)
        self.assertEqual(payload["webhook_name"], "support-hook")
        self.assertEqual(payload["channel_id"], 77)
        self.assertEqual(payload["changed_fields"]["name"]["before"], "old-hook")
        self.assertEqual(payload["changed_fields"]["channel_id"]["after"], 77)
        rendered = repr(payload)
        for secret in ("DO_NOT_STORE_TOKEN", "OLD_SECRET", "NEW_SECRET", "SECRET", "discord.example", "old.invalid", "new.invalid"):
            self.assertNotIn(secret, rendered)

    def test_alert_is_built_from_safe_payload_and_describes_changes(self):
        text = pulse_webhook_audit.alert_text({
            "action": "webhook_update", "webhook_id": 9, "webhook_name": "support",
            "channel_name": "team-support", "actor_id": 42,
            "changed_fields": {"name": {}, "channel_id": {}},
            "token": "NEVER_PRINT", "url": "https://never.invalid",
        })
        self.assertIn("Webhook geändert", text)
        self.assertIn("Name geändert", text)
        self.assertIn("Kanal geändert", text)
        self.assertNotIn("NEVER_PRINT", text)
        self.assertNotIn("never.invalid", text)



    def test_trusted_webhook_actor_is_audited_without_immediate_alert(self):
        async def scenario():
            actor = SimpleNamespace(id=10)
            target = SimpleNamespace(id=902, name="trusted-hook", channel_id=77)
            entry = SimpleNamespace(
                id=8, user=actor, target=target, action=discord.AuditLogAction.webhook_create,
                before=None, after=SimpleNamespace(name="trusted-hook", channel_id=77),
            )

            async def audit_logs(**_kwargs):
                yield entry

            guild = SimpleNamespace(
                id=7, owner_id=10,
                me=SimpleNamespace(guild_permissions=SimpleNamespace(view_audit_log=True)),
                get_member=lambda _user_id: SimpleNamespace(roles=[]),
                audit_logs=audit_logs,
            )
            cog = PulseAntiNuke(SimpleNamespace(user=SimpleNamespace(id=20)))
            cog._config = lambda: {
                "enabled": True, "events": ["webhook_create", "webhook_update", "webhook_delete"],
                "action_limit": 10, "window_seconds": 30, "response": "alert", "mode": "alert",
            }
            records = []
            cog._record = Mock(side_effect=lambda title, actor_id, actor_name, payload:
                                records.append((title, actor_id, actor_name, payload)))
            cog._dispatch = AsyncMock()
            cog._alert = AsyncMock(return_value=True)

            await cog.on_webhooks_update(SimpleNamespace(guild=guild))
            self.assertTrue(any(item[0] == "Webhook-Änderung erkannt" for item in records))
            cog._alert.assert_not_awaited()
            cog._dispatch.assert_not_awaited()

        asyncio.run(scenario())

    def test_webhook_events_are_counted_in_security_cockpit_stats(self):
        from datetime import datetime, timezone
        import pulse_guard_admin

        rows = [{
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "event_type": "Webhook-Änderung erkannt",
            "payload": {"action": "webhook_create"},
        }]
        self.assertEqual(pulse_guard_admin.stats(rows)["webhooks_7d"], 1)

class WebhookAuditIntegrationTests(unittest.TestCase):
    def test_webhook_gateway_event_writes_separate_audit_and_throttled_alert(self):
        async def scenario():
            actor = SimpleNamespace(id=42)
            member = SimpleNamespace(roles=[])
            target = SimpleNamespace(
                id=901, name="support-hook", channel_id=77,
                channel=SimpleNamespace(id=77, name="support"),
                type=SimpleNamespace(name="incoming"),
                token="DO_NOT_STORE_TOKEN",
                url="https://discord.example/api/webhooks/901/SECRET",
            )
            entries = [
                SimpleNamespace(
                    id=entry_id, user=actor, target=target,
                    action=discord.AuditLogAction.webhook_create,
                    before=None, after=SimpleNamespace(name="support-hook", channel_id=77),
                )
                for entry_id in (1, 2)
            ]

            async def audit_logs(**_kwargs):
                for entry in entries:
                    yield entry

            guild = SimpleNamespace(
                id=7, owner_id=10,
                me=SimpleNamespace(guild_permissions=SimpleNamespace(view_audit_log=True)),
                get_member=lambda _user_id: member,
                audit_logs=audit_logs,
            )
            cog = PulseAntiNuke(SimpleNamespace(user=SimpleNamespace(id=20)))
            cog._config = lambda: {
                "enabled": True,
                "events": ["webhook_create", "webhook_update", "webhook_delete"],
                "action_limit": 10, "window_seconds": 30, "response": "alert", "mode": "alert",
            }
            records = []
            cog._record = Mock(side_effect=lambda title, actor_id, actor_name, payload:
                                records.append((title, actor_id, actor_name, payload)))
            cog._dispatch = AsyncMock()
            cog._alert = AsyncMock(return_value=True)
            channel = SimpleNamespace(guild=guild)
            await cog.on_webhooks_update(channel)
            await cog.on_webhooks_update(channel)

            webhook_events = [item for item in records if item[0] == "Webhook-Änderung erkannt"]
            self.assertEqual(len(webhook_events), 2)
            self.assertEqual(cog._alert.await_count, 1)
            self.assertNotIn("DO_NOT_STORE_TOKEN", repr(webhook_events))
            self.assertNotIn("discord.example", repr(webhook_events))

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
