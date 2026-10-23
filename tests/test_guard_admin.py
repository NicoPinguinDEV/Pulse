"""Verwaltung → Sicherheit (/admin/security): access, validation, persistence, tester, quarantine,
plus the extended AutoMod/Anti-Nuke behaviour (observe mode, escalation, quarantine, webhook bursts)."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_moderation as pm
import webserver as ws
from cogs.antinuke import PulseAntiNuke, load_quarantine
from cogs.automod import PulseAutoMod
from tests.fakes import FakeMember
from tests.test_pulse_admin_area import ADMIN, GUILD, TEAMLER, fake_auth_for

ROLE = SimpleNamespace(id=222222222222222222, name="Moderation", position=3, managed=False,
                       permissions=SimpleNamespace(administrator=False, ban_members=True), is_default=lambda: False)
GUARD_GUILD = SimpleNamespace(**{**GUILD.__dict__, "roles": [ROLE], "categories": []})


def auth_with(perms, config=None):
    base = fake_auth_for(perms)

    def fake(request, session, perm="can_view_dashboard", admin=False):
        ctx = base(request, session, perm, admin)
        ctx.guild = GUARD_GUILD
        ctx.config = config if config is not None else ws.load_config()
        return ctx
    return fake


AUTOMOD_FORM = {
    "automod_enabled": "1", "mode": "observe", "automod_rules": ["keywords", "caps"],
    "mention_limit": "6", "flood_messages": "6", "flood_seconds": "10", "duplicate_limit": "3",
    "caps_percent": "80", "caps_min_letters": "10", "blocked_terms": "Böses Wort\nböses wort",
    "blocked_domains": "www.bad.example", "allowed_domains": "", "warn_dm": "1",
    "timeout_after_strikes": "2", "timeout_minutes": "10", "timeout_max_minutes": "60",
    "strike_window_minutes": "60", "alert_channel_id": str(GUILD.text_channels[0].id),
    "automod_exempt_role_ids": [str(ROLE.id)],
}
ANTINUKE_FORM = {
    "antinuke_enabled": "1", "antinuke_events": ["ban", "bot_add"], "action_limit": "4", "window_seconds": "15",
    "response": "quarantine", "kick_unverified_bots": "1", "notify_admins": "1",
    "trusted_role_ids": [str(ROLE.id)], "trusted_user_ids": "111111111111111111, 111111111111111111",
    "alert_channel_id": "",
}


class GuardAdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "config.json"
        self.patches = [
            patch.object(ws, "CONFIG_FILE", str(self.config_path)),
            patch.object(ws, "auth", auth_with(ADMIN)),
            patch.object(ws, "log_audit", lambda *a, **k: None),
            patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def saved(self):
        return json.loads(self.config_path.read_text(encoding="utf-8"))

    def test_page_lists_all_sections_and_hub_links_it(self):
        response = self.client.get("/admin/security")
        self.assertEqual(response.status_code, 200)
        for text in ("Schutzstatus", "AutoMod", "Anti-Nuke", "Regeltester", "Quarantäne", "Vorfallprotokoll", "Schutzwert"):
            self.assertIn(text, response.text)
        self.assertIn('href="/admin/security"', self.client.get("/admin").text)

    def test_old_route_redirects_and_team_members_are_refused_everywhere(self):
        old = self.client.get("/security", follow_redirects=False)
        self.assertEqual((old.status_code, old.headers["location"]), (303, "/admin/security"))
        with patch.object(ws, "auth", auth_with(TEAMLER)):
            self.assertEqual(self.client.get("/admin/security").status_code, 403)
            self.assertEqual(self.client.get("/security").status_code, 403)
            for path in ("/admin/security/automod", "/admin/security/antinuke", "/admin/security/test",
                         "/admin/security/quarantine/restore", "/admin/security/quarantine/dismiss"):
                self.assertEqual(self.client.post(path, data={}).status_code, 403, path)
        self.assertFalse(self.config_path.exists())

    def test_automod_is_saved_normalised(self):
        r = self.client.post("/admin/security/automod", data=AUTOMOD_FORM, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertIn("t=ok", r.headers["location"])
        cfg = self.saved()["pulse_automod"]
        self.assertEqual(cfg["mode"], "observe")
        self.assertEqual(cfg["blocked_terms"], ["böses wort"])
        self.assertEqual(cfg["blocked_domains"], ["bad.example"])
        self.assertEqual(cfg["exempt_role_ids"], [ROLE.id])
        self.assertEqual(cfg["alert_channel_id"], GUILD.text_channels[0].id)
        self.assertEqual(cfg, pm.normalize_automod(cfg))

    def test_automod_validation_changes_nothing(self):
        cases = [
            {"blocked_domains": "https://bad.example/x"},
            {"automod_rules": ["links"], "allowed_domains": ""},
            {"automod_exempt_role_ids": ["999"]},
            {"mention_limit": "abc"},
            {"timeout_minutes": "30", "timeout_max_minutes": "10"},
            {"alert_channel_id": "12345"},
            {"automod_rules": []},
        ]
        for change in cases:
            r = self.client.post("/admin/security/automod", data={**AUTOMOD_FORM, **change}, follow_redirects=False)
            self.assertIn("t=err", r.headers["location"], change)
        self.assertFalse(self.config_path.exists())

    def test_antinuke_is_saved_and_validated(self):
        r = self.client.post("/admin/security/antinuke", data=ANTINUKE_FORM, follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        cfg = self.saved()["pulse_antinuke"]
        self.assertEqual((cfg["response"], cfg["action_limit"], cfg["window_seconds"]), ("quarantine", 4, 15))
        self.assertEqual(cfg["trusted_user_ids"], [111111111111111111])
        self.assertTrue(cfg["kick_unverified_bots"])
        bad = self.client.post("/admin/security/antinuke", data={**ANTINUKE_FORM, "antinuke_events": ["ban"]}, follow_redirects=False)
        self.assertIn("t=err", bad.headers["location"])  # bot kick needs the bot_add event
        bad = self.client.post("/admin/security/antinuke", data={**ANTINUKE_FORM, "trusted_user_ids": "123"}, follow_redirects=False)
        self.assertIn("t=err", bad.headers["location"])
        self.assertEqual(self.saved()["pulse_antinuke"], cfg)

    def test_legacy_form_keeps_new_settings(self):
        self.client.post("/admin/security/automod", data=AUTOMOD_FORM)
        self.client.post("/admin/security/antinuke", data=ANTINUKE_FORM)
        legacy = {"automod_enabled": "on", "automod_rules": ["spam"], "mention_limit": "5",
                  "antinuke_enabled": "on", "antinuke_events": ["ban"], "action_limit": "3", "window_seconds": "10"}
        with patch.object(ws, "auth", auth_with(ADMIN, config=self.saved())):
            self.client.post("/security", data=legacy, follow_redirects=False)
        saved = self.saved()
        self.assertEqual(saved["pulse_automod"]["mode"], "observe")
        self.assertEqual(saved["pulse_automod"]["rules"], ["spam"])
        self.assertEqual(saved["pulse_antinuke"]["response"], "quarantine")

    def test_tester_explains_hits_and_stores_nothing(self):
        self.client.post("/admin/security/automod", data={**AUTOMOD_FORM, "automod_rules": ["keywords", "caps", "spam"]})
        r = self.client.post("/admin/security/test", data={"sample": "DAS IST EIN BÖSES WORT HIER", "mentions": "0"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("Würde ausgelöst: Gesperrter Begriff", r.text)
        self.assertIn("Übermäßige Großschreibung", r.text)
        self.assertIn("Nur beobachten", r.text)
        self.assertEqual(pulse_db.events(limit=10), [])
        clean = self.client.post("/admin/security/test", data={"sample": "Hallo zusammen", "mentions": "0"})
        self.assertIn("Keine Regel würde auslösen", clean.text)

    def test_quarantine_actions_need_reason_and_entry(self):
        r = self.client.post("/admin/security/quarantine/dismiss", data={"user_id": "333333333333333333", "reason": "x"}, follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])
        pulse_db.set_setting("pulse_antinuke_quarantine", {"333333333333333333": {"role_ids": [1], "at": "2026-10-23T10:00:00"}})
        r = self.client.post("/admin/security/quarantine/dismiss", data={"user_id": "333333333333333333", "reason": "Konto geprüft"}, follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        self.assertEqual(load_quarantine(), {})
        self.assertEqual(pulse_db.events(event_type="Anti-Nuke: Quarantäne verworfen")[0]["target_id"], "333333333333333333")


class PolicyTests(unittest.TestCase):
    def test_escalation_doubles_up_to_the_maximum(self):
        cfg = pm.normalize_automod({})
        self.assertEqual(pm.escalation_preview(cfg, 6), ["Warnung", "Timeout 10 Min.", "Timeout 20 Min.", "Timeout 40 Min.", "Timeout 60 Min.", "Timeout 60 Min."])

    def test_new_rules(self):
        self.assertEqual(pm.classify_message("WARUM HÖRT MICH HIER NIEMAND", rules=["caps"]), "caps")
        self.assertIsNone(pm.classify_message("OK OK", rules=["caps"]))
        self.assertEqual(pm.classify_message("schau https://evil.example/x", rules=["links"], allowed_domains=["youtube.com"]), "links")
        self.assertIsNone(pm.classify_message("schau https://m.youtube.com/x", rules=["links"], allowed_domains=["youtube.com"]))

    def test_normalise_keeps_ban_default_and_bounds(self):
        cfg = pm.normalize_antinuke({"enabled": True, "action_limit": 999, "trusted_user_ids": ["1", "x", 1]})
        self.assertEqual((cfg["response"], cfg["action_limit"], cfg["trusted_user_ids"]), ("ban", 10, [1]))

    def test_roles_to_strip_respects_hierarchy(self):
        low = SimpleNamespace(id=1, position=2, managed=False, permissions=SimpleNamespace(manage_roles=True), is_default=lambda: False)
        high = SimpleNamespace(id=2, position=9, managed=False, permissions=SimpleNamespace(administrator=True), is_default=lambda: False)
        harmless = SimpleNamespace(id=3, position=1, managed=False, permissions=SimpleNamespace(), is_default=lambda: False)
        self.assertEqual(pm.roles_to_strip([low, high, harmless], 5), [low])
        self.assertEqual(pm.hierarchy_gaps([low, high, harmless], 5), [high])


class CogBehaviourTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def automod_message(self, member, text, mid=1):
        guild = SimpleNamespace(id=1, owner_id=999, name="Testserver", get_channel=lambda _cid: None)
        return SimpleNamespace(id=mid, guild=guild, author=member, webhook_id=None, channel=SimpleNamespace(id=456),
                               clean_content=text, content=text, mentions=[], role_mentions=[], mention_everyone=False,
                               delete=AsyncMock())

    def test_observe_mode_deletes_nothing_but_logs(self):
        async def scenario():
            member = FakeMember(123, "Test")
            member.guild_permissions.manage_messages = False
            member.timeout, member.send = AsyncMock(), AsyncMock()
            cog = PulseAutoMod(SimpleNamespace())
            cog._config = lambda: {"enabled": True, "mode": "observe", "rules": ["keywords"], "blocked_terms": ["xyz"]}
            msg = self.automod_message(member, "xyz")
            with patch("cogs.automod.discord.Member", FakeMember), patch("builtins.print"):
                await cog.on_message(msg)
            msg.delete.assert_not_awaited()
            member.send.assert_not_awaited()
        asyncio.run(scenario())
        event = pulse_db.events(event_type="AutoMod-Maßnahme")[0]
        self.assertEqual(json.loads(event["payload_json"])["mode"], "observe")

    def test_third_violation_gets_a_doubled_timeout_and_edits_are_checked(self):
        async def scenario():
            member = FakeMember(123, "Test")
            member.guild_permissions.manage_messages = False
            member.timeout, member.send = AsyncMock(), AsyncMock()
            cog = PulseAutoMod(SimpleNamespace())
            cog._config = lambda: {"enabled": True, "rules": ["keywords"], "blocked_terms": ["xyz"]}
            with patch("cogs.automod.discord.Member", FakeMember), patch("builtins.print"):
                await cog.on_message(self.automod_message(member, "xyz", 1))
                await cog.on_message(self.automod_message(member, "xyz", 2))
                before = self.automod_message(member, "harmlos", 3)
                after = self.automod_message(member, "jetzt xyz", 3)
                await cog.on_message_edit(before, after)
            after.delete.assert_awaited()
            self.assertEqual([c.args[0].total_seconds() for c in member.timeout.await_args_list], [600, 1200])
        asyncio.run(scenario())

    def antinuke_guild(self, entries, member):
        bot_member = SimpleNamespace(guild_permissions=SimpleNamespace(view_audit_log=True, manage_roles=True, ban_members=True),
                                     top_role=SimpleNamespace(position=10))

        async def audit_logs(**_kwargs):
            for entry in entries:
                yield entry
        return SimpleNamespace(id=7, owner_id=10, me=bot_member, members=[], audit_logs=audit_logs,
                               get_member=lambda uid: member if uid == member.id else None, ban=AsyncMock(),
                               get_role=lambda _rid: None)

    def test_quarantine_strips_dangerous_roles_and_webhook_bursts_count(self):
        danger = SimpleNamespace(id=50, name="Admin-light", position=5, managed=False,
                                 permissions=SimpleNamespace(manage_webhooks=True), is_default=lambda: False)
        plain = SimpleNamespace(id=51, name="Bürger", position=1, managed=False, permissions=SimpleNamespace(), is_default=lambda: False)
        member = FakeMember(30, "Angreifer", roles=[danger, plain])
        actor = SimpleNamespace(id=30)
        import discord
        entries = [SimpleNamespace(id=i, user=actor, target=SimpleNamespace(id=900 + i), action=discord.AuditLogAction.webhook_create)
                   for i in (1, 2, 3)]

        class Pos:
            def __init__(self, p):
                self.position = p

            def __ge__(self, other):
                return self.position >= other.position
        member.top_role = Pos(5)
        guild = self.antinuke_guild(entries, member)
        guild.me.top_role = Pos(10)

        async def scenario():
            cog = PulseAntiNuke(SimpleNamespace(user=SimpleNamespace(id=20)))
            cog._config = lambda: {"enabled": True, "events": ["webhook_create", "webhook_delete", "webhook_update"],
                                   "action_limit": 3, "window_seconds": 10, "response": "quarantine"}
            cog._alert = AsyncMock()
            channel = SimpleNamespace(guild=guild)
            with patch("builtins.print"):
                for _ in range(3):
                    await cog.on_webhooks_update(channel)
            guild.ban.assert_not_awaited()
            return cog
        asyncio.run(scenario())
        self.assertEqual(member.removed_roles, [danger])
        entry = load_quarantine()["30"]
        self.assertEqual(entry["role_ids"], [50])
        self.assertEqual(len(pulse_db.events(event_type="Anti-Nuke-Aktion erkannt")), 3)

    def test_alert_response_never_bans(self):
        actor = SimpleNamespace(id=30)
        member = FakeMember(30, "X")
        entries = [SimpleNamespace(id=i, user=actor, target=SimpleNamespace(id=99)) for i in (1, 2)]
        guild = self.antinuke_guild(entries, member)

        async def scenario():
            cog = PulseAntiNuke(SimpleNamespace(user=SimpleNamespace(id=20)))
            cog._config = lambda: {"enabled": True, "events": ["ban"], "action_limit": 2, "window_seconds": 10, "response": "alert"}
            cog._alert = AsyncMock()
            with patch("builtins.print"):
                await cog._handle_event(guild, "ban", 99)
                await cog._handle_event(guild, "ban", 99)
            guild.ban.assert_not_awaited()
            self.assertTrue(any("nur melden" in c.args[2] for c in cog._alert.await_args_list))
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
