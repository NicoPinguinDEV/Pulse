"""K-12: the moderation log (edits, voice, roles) uses the channel configured in the dashboard."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import webserver as ws
from cogs import advanced_logs
from tests.test_pulse_admin_area import ADMIN, CHANNEL, GUILD, TEAMLER, fake_auth_for


def fake_message(content, guild, channel=CHANNEL):
    return SimpleNamespace(
        id=987654321,
        content=content,
        guild=guild,
        channel=channel,
        attachments=[],
        author=SimpleNamespace(id=1, bot=False, mention="<@1>", roles=[]),
    )


class AdvancedLogsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
                        patch.object(ws, "BASE_DIR", Path(self.tmp.name)),
                        patch.object(ws, "log_audit", lambda *a, **k: None),
                        patch("builtins.print")]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        advanced_logs._HINT_SHOWN = False
        self.sent = []
        # Each event test gets isolated guild/channel objects. The dashboard route below
        # still uses the shared admin-test fixture and its temporary send method.
        self.channel = SimpleNamespace(
            id=CHANNEL.id, name=CHANNEL.name, position=CHANNEL.position,
            send=lambda *args, **kwargs: None,
        )
        self.guild = SimpleNamespace(
            **dict(
                vars(GUILD), channels=[self.channel], text_channels=[self.channel],
                get_channel=lambda channel_id: self.channel if int(channel_id) == self.channel.id else None,
            )
        )
        self._channel_had_send = hasattr(CHANNEL, "send")
        if not self._channel_had_send:
            CHANNEL.send = lambda *args, **kwargs: None

        async def fake_send(channel, **kwargs):
            self.sent.append((channel, kwargs))
        self.send_patch = patch.object(advanced_logs, "send_styled", fake_send)
        self.send_patch.start()
        self.cog = advanced_logs.AdvancedLogs(SimpleNamespace())

    def tearDown(self):
        self.send_patch.stop()
        if not self._channel_had_send:
            delattr(CHANNEL, "send")
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def edit(self):
        asyncio.run(self.cog.on_message_edit(fake_message("alt", self.guild, self.channel), fake_message("neu", self.guild, self.channel)))

    def test_configured_channel_receives_logs(self):
        pulse_db.set_setting(advanced_logs.LOG_SETTING_KEY, str(self.channel.id))
        self.edit()
        self.assertEqual(len(self.sent), 1)
        self.assertIs(self.sent[0][0], self.channel)
        logged_embed=self.sent[0][1]["embed"]
        self.assertEqual(logged_embed.title, "Nachricht bearbeitet")
        self.assertNotIn("alt", logged_embed.description or "")
        self.assertNotIn("neu", logged_embed.description or "")
        self.assertTrue(any("Datenschutz" == field.name for field in logged_embed.fields))

    def test_unconfigured_logs_nothing_and_hints_once(self):
        with patch("builtins.print") as printed:
            self.edit()
            self.edit()
        self.assertEqual(self.sent, [])
        hints = [c for c in printed.call_args_list if "Log-Kanal" in str(c)]
        self.assertEqual(len(hints), 1)
        self.assertIn("nicht eingerichtet", advanced_logs.log_channel_status(GUILD))

    def test_deleted_message_records_attachment_names_but_not_body(self):
        pulse_db.set_setting(advanced_logs.LOG_SETTING_KEY, str(self.channel.id))
        message = fake_message("PRIVATE MESSAGE BODY", self.guild, self.channel)
        message.attachments = [SimpleNamespace(filename="evidence.png"), SimpleNamespace(filename="report.pdf")]

        asyncio.run(self.cog.on_message_delete(message))

        self.assertEqual(len(self.sent), 1)
        embed = self.sent[0][1]["embed"]
        self.assertEqual(embed.title, "Nachricht gelöscht")
        serialized = " ".join([embed.description or ""] + [field.value for field in embed.fields])
        self.assertIn("evidence.png", serialized)
        self.assertIn("report.pdf", serialized)
        self.assertNotIn("PRIVATE MESSAGE BODY", serialized)

    def test_disabled_log_category_is_respected(self):
        pulse_db.set_setting(advanced_logs.LOG_SETTING_KEY, str(self.channel.id))
        pulse_db.set_setting(advanced_logs.LOG_CONFIG_SETTING_KEY, {"enabled": {"messages": False}})
        asyncio.run(self.cog.on_message_delete(fake_message("not retained", self.guild, self.channel)))
        self.assertEqual(self.sent, [])

    def test_nickname_changes_are_not_suppressed_when_role_logs_are_disabled(self):
        pulse_db.set_setting(advanced_logs.LOG_SETTING_KEY, str(CHANNEL.id))
        pulse_db.set_setting(advanced_logs.LOG_CONFIG_SETTING_KEY, {"enabled": {"roles": False, "members": True}})
        old_role = SimpleNamespace(id=10, mention="<@&10>")
        new_role = SimpleNamespace(id=11, mention="<@&11>")
        before = SimpleNamespace(
            id=5, mention="<@5>", name="Nutzer", nick=None, roles=[old_role],
            bot=False, guild=GUILD,
        )
        after = SimpleNamespace(
            id=5, mention="<@5>", name="Nutzer", nick="Neuer Name", roles=[new_role],
            bot=False, guild=GUILD,
        )

        asyncio.run(self.cog.on_member_update(before, after))

        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1]["embed"].title, "Nickname geändert")

    def test_voice_channel_move_is_recorded(self):
        pulse_db.set_setting(advanced_logs.LOG_SETTING_KEY, str(self.channel.id))
        member = SimpleNamespace(
            id=1, mention="<@1>", bot=False, roles=[], guild=self.guild,
        )
        old = SimpleNamespace(channel=SimpleNamespace(id=200, name="Support-Warteraum"))
        new = SimpleNamespace(channel=SimpleNamespace(id=201, name="Support"))
        asyncio.run(self.cog.on_voice_state_update(member, old, new))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0][1]["embed"].title, "Sprachkanal gewechselt")
        self.assertIn("Support-Warteraum", self.sent[0][1]["embed"].description)
        self.assertIn("Support", self.sent[0][1]["embed"].description)

    def test_audit_event_includes_actor_target_and_reason(self):
        pulse_db.set_setting(advanced_logs.LOG_SETTING_KEY, str(self.channel.id))
        actor = SimpleNamespace(id=2, mention="<@2>", bot=False, roles=[])
        target = SimpleNamespace(id=3, mention="<@3>", bot=False, roles=[])
        entry = SimpleNamespace(
            guild=self.guild,
            action=SimpleNamespace(name="ban"),
            target=target,
            user=actor,
            changes=SimpleNamespace(before=SimpleNamespace(), after=SimpleNamespace()),
            reason="Testgrund",
            id=1001,
        )
        asyncio.run(self.cog.on_audit_log_entry_create(entry))
        self.assertEqual(len(self.sent), 1)
        embed = self.sent[0][1]["embed"]
        self.assertEqual(embed.title, "Mitglied gebannt")
        serialized = " ".join(field.value for field in embed.fields)
        self.assertIn("<@2>", serialized)
        self.assertIn("<@3>", serialized)
        self.assertIn("Testgrund", serialized)

    def test_dashboard_saves_channel_and_rp_roles_admin_only(self):
        client = TestClient(ws.app)
        role = SimpleNamespace(id=222222222222222222, name="Leitstelle", position=3, managed=False)
        guild = SimpleNamespace(**dict(vars(GUILD), roles=[role], get_role=lambda rid: role if rid == role.id else None))

        def auth(perms):
            base = fake_auth_for(perms)

            def wrapped(*a, **k):
                ctx = base(*a, **k)
                ctx.guild = guild
                return ctx
            return wrapped

        with patch.object(ws, "auth", auth(ADMIN)):
            self.assertIn("Moderations-Log nicht eingerichtet", client.get("/system").text)
        data = {"log_channel_id": str(self.channel.id), "rp_role_ids": [str(role.id), "999"]}
        with patch.object(ws, "auth", auth(TEAMLER)):
            self.assertEqual(client.post("/admin/discord/logs", data=data, follow_redirects=False).status_code, 403)
        self.assertIsNone(pulse_db.get_setting(advanced_logs.LOG_SETTING_KEY, None))
        with patch.object(ws, "auth", auth(ADMIN)):
            bad = client.post("/admin/discord/logs", data={"log_channel_id": "999999999999999999"}, follow_redirects=False)
            self.assertIn("t=err", bad.headers["location"])
            ok = client.post("/admin/discord/logs", data=data, follow_redirects=False)
            self.assertIn("t=ok", ok.headers["location"])
            html = client.get("/admin/discord").text
        self.assertEqual(pulse_db.get_setting(advanced_logs.LOG_SETTING_KEY), str(self.channel.id))
        self.assertEqual(pulse_db.get_setting("rp_panel_role_ids"), [str(role.id)])  # unknown role dropped
        self.assertIn("Moderations-Log", html)
        self.assertIn("Leitstelle", html)

        # The advanced log controls are saved through the protected settings handler.
        settings_data = {
            "main_log_channel_id": str(self.channel.id),
            "log_enabled_messages": "1",
            "log_enabled_members": "1",
            "log_channel_messages": str(self.channel.id),
            "log_channel_members": "0",
            "log_ignore_bots": "1",
            "log_ignore_channels": [str(self.channel.id)],
            "log_ignore_roles": [str(role.id)],
        }
        with patch.object(ws, "auth", auth(TEAMLER)):
            denied = client.post("/settings/advanced-logs/save", data=settings_data, follow_redirects=False)
            self.assertEqual(denied.status_code, 403)
        with patch.object(ws, "auth", auth(ADMIN)):
            invalid_route = client.post(
                "/settings/advanced-logs/save",
                data={"main_log_channel_id": "0", "log_enabled_messages": "1", "log_channel_messages": "0"},
                follow_redirects=False,
            )
            self.assertIn("t=err", invalid_route.headers["location"])
            saved = client.post("/settings/advanced-logs/save", data=settings_data, follow_redirects=False)
        self.assertEqual(saved.status_code, 303)
        self.assertEqual(pulse_db.get_setting(advanced_logs.LOG_SETTING_KEY), str(self.channel.id))
        saved_cfg = pulse_db.get_setting(advanced_logs.LOG_CONFIG_SETTING_KEY)
        self.assertTrue(saved_cfg["enabled"]["messages"])
        self.assertTrue(saved_cfg["enabled"]["members"])
        self.assertEqual(saved_cfg["channels"]["messages"], CHANNEL.id)
        self.assertIn(CHANNEL.id, saved_cfg["ignore_channel_ids"])
        self.assertIn(role.id, saved_cfg["ignore_role_ids"])
        self.assertTrue(saved_cfg["ignore_bots"])


if __name__ == "__main__":
    unittest.main()
