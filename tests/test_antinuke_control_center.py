"""Anti-Nuke Control Center: individual rules (counting, windows, actors, categories, dedupe), action chains,
modes, exemptions, lockdown, and the dashboard routes (access, validation, persistence, conflicts).

Only local fakes – no Discord connection, temp config and database.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from fastapi.testclient import TestClient

import pulse_db
import pulse_moderation as pm
import webserver as ws
from cogs import antinuke
from cogs.antinuke import PulseAntiNuke, lift_lockdown, load_lockdown, start_lockdown
from tests.fakes import FakeMember
from tests.test_guard_admin import ROLE, auth_with
from tests.test_pulse_admin_area import ADMIN, GUILD, TEAMLER

OWNER, BOT, ACTOR, OTHER = 10, 20, 30, 31
CHANNEL_DELETE = discord.AuditLogAction.channel_delete
ROLE_DELETE = discord.AuditLogAction.role_delete


class Pos:
    def __init__(self, position):
        self.position = position

    def __ge__(self, other):
        return self.position >= other.position


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def entry(entry_id, actor_id, action=CHANNEL_DELETE, target=900):
    return SimpleNamespace(id=entry_id, user=SimpleNamespace(id=actor_id), target=SimpleNamespace(id=target), action=action)


class FakeGuild:
    """Audit log = every entry appended so far (the cog skips entries it has already counted)."""

    def __init__(self, members=(), perms=None):
        perms = perms or {}
        self.id, self.owner_id = 7, OWNER
        self.entries = []
        self.me = SimpleNamespace(id=BOT, top_role=Pos(10), guild_permissions=SimpleNamespace(
            view_audit_log=True, ban_members=perms.get("ban", True), kick_members=True, manage_roles=perms.get("roles", True)))
        self.members = list(members)
        self.ban, self.kick = AsyncMock(), AsyncMock()
        self.audit_calls = 0
        self.channels = {}
        self.default_role = SimpleNamespace(id=7, name="@everyone")

    async def audit_logs(self, **kwargs):
        self.audit_calls += 1
        for item in list(self.entries):
            if kwargs.get("action") in (None, item.action):
                yield item

    def get_member(self, uid):
        return next((m for m in self.members if m.id == uid), None)

    def get_role(self, _rid):
        return None

    def get_channel(self, cid):
        return self.channels.get(cid)


def rule(rule_id, events, limit, window, actions, **extra):
    return {"id": rule_id, "label": rule_id, "events": events, "limit": limit, "window_seconds": window, "actions": actions, **extra}


class RuleEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()
        self.clock = Clock()
        self.patches = [patch("cogs.antinuke.time.monotonic", self.clock), patch("builtins.print"),
                        patch.object(antinuke, "LOCK_EDIT_PAUSE", 0)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.db.stop()
        self.tmp.cleanup()

    def cog(self, config):
        cog = PulseAntiNuke(SimpleNamespace(user=SimpleNamespace(id=BOT)))
        cog._config = lambda: config
        cog._alert = AsyncMock(return_value=True)
        return cog

    def burst(self, cog, guild, actor_ids, action=CHANNEL_DELETE, key="channel_delete", step=1.0, start_id=1):
        async def run():
            for offset, actor in enumerate(actor_ids):
                guild.entries.append(entry(start_id + offset, actor, action))
                await cog._handle_event(guild, key, None)
                self.clock.now += step
        asyncio.run(run())

    def config(self, *rules, mode="protect", **extra):
        return {"enabled": True, "mode": mode, "events": [], "rules": list(rules), **extra}

    def test_two_deletions_below_three_do_nothing_the_third_bans(self):
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 3, 10, ["log", "alert", "ban"])))
        guild = FakeGuild()
        self.burst(cog, guild, [ACTOR, ACTOR])
        guild.ban.assert_not_awaited()
        self.burst(cog, guild, [ACTOR], start_id=3)
        guild.ban.assert_awaited_once()
        self.assertEqual(guild.ban.await_args.args[0].id, ACTOR)
        self.assertIn("Regel „kanal“", guild.ban.await_args.kwargs["reason"])
        event = pulse_db.events(event_type="Anti-Nuke: Akteur gesperrt")[0]
        steps = {s["action"]: s["status"] for s in json.loads(event["payload_json"])["steps"]}
        self.assertEqual((steps["ban"], steps["log"], steps["alert"]), ("ok", "ok", "ok"))

    def test_events_outside_the_window_no_longer_count(self):
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 3, 10, ["log", "ban"])))
        guild = FakeGuild()
        self.burst(cog, guild, [ACTOR, ACTOR], step=6)  # t=0, t=6
        self.clock.now += 10  # third one 22 s after the first
        self.burst(cog, guild, [ACTOR], start_id=3)
        guild.ban.assert_not_awaited()

    def test_different_actors_do_not_share_a_counter(self):
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 3, 10, ["log", "ban"])))
        guild = FakeGuild()
        self.burst(cog, guild, [ACTOR, OTHER, ACTOR, OTHER])
        guild.ban.assert_not_awaited()

    def test_categories_use_their_own_thresholds_and_override_the_global_rule(self):
        config = self.config(rule("kanal", ["channel_delete"], 5, 10, ["log", "ban"]),
                             rule("rolle", ["role_delete"], 2, 10, ["log", "ban"]),
                             events=["channel_delete", "role_delete"], action_limit=2)
        cog = self.cog(config)
        guild = FakeGuild()
        self.burst(cog, guild, [ACTOR, ACTOR])  # 2 channel deletions: the global 2 must NOT fire (overridden)
        guild.ban.assert_not_awaited()
        self.burst(cog, guild, [ACTOR, ACTOR], action=ROLE_DELETE, key="role_delete", start_id=10)
        guild.ban.assert_awaited_once()
        self.assertEqual(json.loads(pulse_db.events(event_type="Anti-Nuke: Akteur gesperrt")[0]["payload_json"])["rule_id"], "rolle")
        self.assertEqual([r["id"] for r in pm.effective_rules(pm.normalize_antinuke(config))], ["kanal", "rolle"])

    def test_the_same_audit_entry_is_counted_once(self):
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 3, 10, ["log", "ban"])))
        guild = FakeGuild()
        guild.entries.append(entry(1, ACTOR))

        async def run():
            for _ in range(4):  # four Discord events, but only one audit-log entry
                await cog._handle_event(guild, "channel_delete", None)
        asyncio.run(run())
        guild.ban.assert_not_awaited()
        self.assertEqual(len(pulse_db.events(event_type="Anti-Nuke-Aktion erkannt")), 1)

    def test_trusted_users_roles_and_rule_exemptions(self):
        trusted_role = SimpleNamespace(id=55, name="Leitung", position=2, permissions=SimpleNamespace())
        role_member = FakeMember(OTHER, "Leitung", roles=[trusted_role])
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 2, 10, ["log", "ban"], exempt_user_ids=[32]),
                                   trusted_user_ids=[ACTOR], trusted_role_ids=[55]))
        guild = FakeGuild(members=[role_member])
        self.burst(cog, guild, [ACTOR, ACTOR, OTHER, OTHER, 32, 32])
        guild.ban.assert_not_awaited()
        trusted = [json.loads(e["payload_json"])["trusted"] for e in pulse_db.events(event_type="Anti-Nuke-Aktion erkannt")]
        self.assertEqual(sorted(trusted), [False, False, True, True, True, True])

    def test_owner_and_bot_are_never_sanctioned(self):
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 2, 10, ["log", "ban", "quarantine"])))
        guild = FakeGuild()
        self.burst(cog, guild, [OWNER, OWNER, BOT, BOT])
        guild.ban.assert_not_awaited()

        async def direct():  # defence in depth: even a forced chain skips the protected actor
            config = pm.normalize_antinuke(self.config(rule("kanal", ["channel_delete"], 2, 10, ["log", "ban"])))
            return await cog._run_rule(guild, config, pm.effective_rules(config)[0], SimpleNamespace(id=OWNER), None, "channel_delete")
        payload = asyncio.run(direct())
        guild.ban.assert_not_awaited()
        self.assertEqual([s["status"] for s in payload["steps"] if s["action"] == "ban"], ["skipped"])

    def test_disabled_protection_does_not_even_read_the_audit_log(self):
        config = self.config(rule("kanal", ["channel_delete"], 1, 10, ["log", "ban"]))
        config["enabled"] = False
        cog = self.cog(config)
        guild = FakeGuild()
        self.burst(cog, guild, [ACTOR, ACTOR, ACTOR])
        guild.ban.assert_not_awaited()
        self.assertEqual(guild.audit_calls, 0)

    def test_alert_mode_reports_but_never_sanctions(self):
        member = FakeMember(ACTOR, "X")
        member.top_role = Pos(1)
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 2, 10, ["log", "alert", "ban", "quarantine"]), mode="alert",
                                   kick_unverified_bots=True))
        guild = FakeGuild(members=[member])
        self.burst(cog, guild, [ACTOR, ACTOR])
        guild.ban.assert_not_awaited()
        self.assertEqual(member.removed_roles, [])
        texts = [c.args[2] for c in cog._alert.await_args_list]
        self.assertTrue(any("NICHT ausgeführt" in t for t in texts), texts)
        payload = json.loads(pulse_db.events(event_type="Anti-Nuke ausgelöst")[0]["payload_json"])
        self.assertTrue(payload["dry_run"])
        self.assertEqual({s["action"]: s["status"] for s in payload["steps"]}["ban"], "skipped")

    def test_rule_test_mode_only_reports_even_in_protect_mode(self):
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 2, 10, ["log", "ban"], test_mode=True)))
        guild = FakeGuild()
        self.burst(cog, guild, [ACTOR, ACTOR])
        guild.ban.assert_not_awaited()

    def test_discord_refusal_is_logged_as_failed_and_other_steps_continue(self):
        danger = SimpleNamespace(id=60, name="Mod", position=3, managed=False, permissions=SimpleNamespace(ban_members=True),
                                 is_default=lambda: False)
        member = FakeMember(ACTOR, "X", roles=[danger])
        member.top_role = Pos(3)
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 2, 10, ["log", "alert", "quarantine", "ban"])))
        guild = FakeGuild(members=[member])
        guild.ban.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "nope")
        self.burst(cog, guild, [ACTOR, ACTOR])
        self.assertEqual(member.removed_roles, [danger])  # the step before the failing ban still ran
        payload = json.loads(pulse_db.events(event_type="Anti-Nuke ausgelöst")[0]["payload_json"])
        steps = {s["action"]: s for s in payload["steps"]}
        self.assertEqual((steps["quarantine"]["status"], steps["ban"]["status"]), ("ok", "failed"))
        self.assertIn("abgelehnt", steps["ban"]["detail"])
        self.assertFalse(pulse_db.events(event_type="Anti-Nuke: Akteur gesperrt"))  # never reported as done
        self.assertTrue(any("❌" in c.args[2] for c in cog._alert.await_args_list))

    def test_no_double_ban_and_no_repeated_chain(self):
        cog = self.cog(self.config(rule("a", ["channel_delete"], 2, 10, ["log", "ban"]),
                                   rule("b", ["channel_delete"], 2, 10, ["log", "ban"])))
        guild = FakeGuild()
        self.burst(cog, guild, [ACTOR] * 6)  # both rules fire once; later deletions are in the cooldown
        guild.ban.assert_awaited_once()
        triggered = pulse_db.events(event_type="Anti-Nuke: Akteur gesperrt") + pulse_db.events(event_type="Anti-Nuke ausgelöst")
        self.assertEqual(len(triggered), 2)
        skipped = [s for e in triggered for s in json.loads(e["payload_json"])["steps"] if s["status"] == "skipped"]
        self.assertEqual([s["detail"] for s in skipped], ["bereits ausgeführt (Aktions-Cooldown)"])

    def test_detections_and_triggered_rules_reach_the_automation_engine(self):
        import pulse_ultimate
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 2, 10, ["log", "alert"])))
        cog._dispatch = PulseAntiNuke._dispatch.__get__(cog)
        guild = FakeGuild()
        with patch.object(pulse_ultimate, "dispatch_event", AsyncMock(return_value=0)) as dispatch:
            self.burst(cog, guild, [ACTOR, ACTOR])
        triggers = [c.args[1] for c in dispatch.await_args_list]
        self.assertEqual(triggers, ["antinuke_event", "antinuke_event", "antinuke_rule"])
        context = dispatch.await_args_list[-1].args[2]
        self.assertEqual((context["rule_id"], context["actor_id"]), ("kanal", ACTOR))

    def test_counters_stay_bounded(self):
        cog = self.cog(self.config(rule("kanal", ["channel_delete"], 50, 600, ["log"])))
        guild = FakeGuild()
        with patch.object(antinuke, "MAX_TRACKED_ACTORS", 20):
            self.burst(cog, guild, list(range(1000, 1300)), step=0.01)
        self.assertLessEqual(len(cog._rule_events), 20)

    def test_broken_configuration_does_not_crash_the_listener(self):
        cog = self.cog({"enabled": True, "mode": "protect", "rules": "kaputt", "events": ["channel_delete"], "action_limit": "x"})
        guild = FakeGuild()
        self.burst(cog, guild, [ACTOR, ACTOR, ACTOR])  # falls back to the global rule with the default 3
        guild.ban.assert_awaited_once()

    def test_lockdown_is_reversible_and_never_doubled(self):
        everyone_before = discord.PermissionOverwrite(send_messages=True)

        class Channel:
            def __init__(self, cid, overwrite):
                self.id, self.name, self.type, self.ow = cid, f"k{cid}", "text", overwrite
                self.set_permissions = AsyncMock(side_effect=self._set)

            def overwrites_for(self, _role):
                return discord.PermissionOverwrite(**dict(self.ow)) if self.ow is not None else discord.PermissionOverwrite()

            async def _set(self, _role, overwrite=None, reason=None):
                self.ow = overwrite
        guild = FakeGuild()
        guild.channels = {1: Channel(1, everyone_before), 2: Channel(2, None)}

        async def run():
            first = await start_lockdown(guild, [1, 2], reason="Test", by="Test")
            second = await start_lockdown(guild, [1, 2], reason="Test", by="Test")
            locked = (guild.channels[1].ow.send_messages, guild.channels[2].ow.send_messages)
            lifted = await lift_lockdown(guild, by="Admin")
            return first, second, locked, lifted
        first, second, locked, lifted = asyncio.run(run())
        self.assertEqual((first["status"], second["status"], locked), ("ok", "skipped", (False, False)))
        self.assertEqual(lifted["failed"], [])
        self.assertIs(guild.channels[1].ow.send_messages, True)  # original value restored
        self.assertIsNone(guild.channels[2].ow)  # overwrite removed again – it did not exist before
        self.assertFalse(load_lockdown()["active"])


class PolicyTests(unittest.TestCase):
    def test_old_configuration_keeps_its_behaviour(self):
        legacy = pm.normalize_antinuke({"enabled": True, "events": ["ban", "kick"], "action_limit": 4, "window_seconds": 20, "response": "kick"})
        self.assertEqual(legacy["mode"], "protect")
        [global_rule] = pm.effective_rules(legacy)
        self.assertEqual((global_rule["id"], global_rule["events"], global_rule["limit"], global_rule["window_seconds"]),
                         ("global", ["ban", "kick"], 4, 20))
        self.assertIn("kick", global_rule["actions"])
        self.assertEqual(pm.normalize_antinuke({"enabled": True, "response": "alert"})["mode"], "alert")
        fresh = pm.normalize_antinuke({})
        self.assertEqual((fresh["enabled"], fresh["rules"]), (False, []))
        self.assertEqual(pm.normalize_antinuke({"mode": "irgendwas"})["mode"], "alert")

    def test_invalid_rules_are_reported_not_silently_used(self):
        raw = {"rules": [{"id": "ok", "events": ["ban"], "limit": 999, "window_seconds": 1}, {"id": "x"}, "kaputt",
                         {"id": "k", "events": ["ban"], "limit": "abc"}]}
        config = pm.normalize_antinuke(raw)
        self.assertEqual([(r["id"], r["limit"], r["window_seconds"]) for r in config["rules"]], [("ok", 50, 3)])
        self.assertEqual(len(pm.antinuke_problems(raw)), 3)

    def test_description_and_simulation(self):
        config = pm.normalize_antinuke({"enabled": True, "mode": "alert", "rules": [rule("r", ["channel_delete"], 3, 10, ["log", "alert", "ban"])]})
        r = config["rules"][0]
        self.assertIn("innerhalb von 10 Sekunden mindestens 3×", pm.describe_rule(r, config))
        self.assertIn("Alarmmodus", pm.describe_rule(r, config))
        self.assertFalse(pm.simulate_rule(r, config, 2)["triggered"])
        result = pm.simulate_rule(r, config, 3)
        self.assertTrue(result["triggered"])
        self.assertIn(("ban", "nur gemeldet"), result["steps"])


class ControlCenterRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "config.json"
        self.audit = []
        self.patches = [
            patch.object(ws, "CONFIG_FILE", str(self.config_path)),
            patch.object(ws, "auth", auth_with(ADMIN)),
            patch.object(ws, "log_audit", lambda *a, **k: self.audit.append(a)),
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
        return json.loads(self.config_path.read_text(encoding="utf-8"))["pulse_antinuke"]

    def write(self, section):
        self.config_path.write_text(json.dumps({"pulse_antinuke": section}), encoding="utf-8")

    def rule_form(self, **change):
        form = {"label": "Massenlöschung", "events": ["channel_delete"], "limit": "3", "window_seconds": "10",
                "actions": ["alert", "ban"], "severity": "critical", "enabled": "1", "confirm_critical": "1"}
        form.update(change)
        return form

    def test_admin_creates_the_three_in_ten_seconds_rule_and_it_survives_a_restart(self):
        r = self.client.post("/admin/security/antinuke/rules/save", data=self.rule_form(), follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        ws._CONFIG_CACHE.update(key=None, data=None)  # like a restart: read from disk again
        config = pm.normalize_antinuke(ws.load_config()["pulse_antinuke"])
        [saved] = config["rules"]
        self.assertEqual((saved["events"], saved["limit"], saved["window_seconds"], saved["actions"]),
                         (["channel_delete"], 3, 10, ["log", "alert", "ban"]))
        self.assertEqual(config["revision"], 1)
        self.assertEqual(config["updated_by"], "Tester")
        self.assertTrue(any(a[2] == "Anti-Nuke-Regel erstellt" for a in self.audit))
        page = self.client.get("/admin/security")
        self.assertEqual(page.status_code, 200)
        for text in ("Anti-Nuke Control Center", "Massenlöschung", "Individuelle Regeln", "Vertrauenswürdige Personen", "Notfall-Lockdown"):
            self.assertIn(text, page.text)
        # a fresh cog reads the stored rule through the normal config path
        cog = PulseAntiNuke(SimpleNamespace(user=SimpleNamespace(id=BOT)))
        self.assertEqual(pm.normalize_antinuke(cog._config())["rules"][0]["id"], saved["id"])

    def test_invalid_or_unconfirmed_rules_are_refused(self):
        cases = [
            {"limit": "0"}, {"limit": "abc"}, {"window_seconds": "9999"}, {"events": ["gibtsnicht"]}, {"events": []},
            {"actions": ["kick", "ban"]}, {"actions": ["lockdown"]}, {"actions": ["notify_role"]}, {"actions": ["eval"]},
            {"confirm_critical": "0"}, {"label": ""}, {"exempt_user_ids": "123"}, {"severity": "x"},
        ]
        for change in cases:
            r = self.client.post("/admin/security/antinuke/rules/save", data=self.rule_form(**change), follow_redirects=False)
            self.assertIn("t=err", r.headers["location"], change)
        self.assertFalse(self.config_path.exists())

    def test_team_members_can_neither_read_nor_change_anything(self):
        self.write({"enabled": True, "rules": [rule("r1", ["ban"], 2, 10, ["log"])]})
        before = self.config_path.read_text(encoding="utf-8")
        with patch.object(ws, "auth", auth_with(TEAMLER)):
            self.assertEqual(self.client.get("/admin/security").status_code, 403)
            for path in ("/admin/security/antinuke/rules/save", "/admin/security/antinuke/rules/toggle",
                         "/admin/security/antinuke/rules/delete", "/admin/security/antinuke/rules/preset",
                         "/admin/security/antinuke/rules/simulate", "/admin/security/antinuke/trusted",
                         "/admin/security/antinuke/trusted/check", "/admin/security/lockdown/start",
                         "/admin/security/lockdown/lift", "/admin/security/antinuke"):
                data = {**self.rule_form(), "rule_id": "r1", "confirm_delete": "1", "confirm": "1", "reason": "Test-Grund",
                        "preset": "channel_delete", "count": "3"}
                self.assertEqual(self.client.post(path, data=data).status_code, 403, path)
        self.assertEqual(self.config_path.read_text(encoding="utf-8"), before)

    def test_toggle_delete_preset_and_simulation(self):
        self.write({"enabled": True, "mode": "alert", "rules": [rule("r1", ["ban"], 2, 10, ["log", "ban"])]})
        r = self.client.post("/admin/security/antinuke/rules/toggle", data={"rule_id": "r1"}, follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        self.assertFalse(self.saved()["rules"][0]["enabled"])
        sim = self.client.post("/admin/security/antinuke/rules/simulate", data={"rule_id": "r1", "count": "5"})
        self.assertIn("Regel ist deaktiviert", sim.text)
        r = self.client.post("/admin/security/antinuke/rules/delete", data={"rule_id": "r1", "confirm_delete": "0"}, follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])
        self.client.post("/admin/security/antinuke/rules/delete", data={"rule_id": "r1", "confirm_delete": "1"})
        self.assertEqual(self.saved()["rules"], [])
        self.client.post("/admin/security/antinuke/rules/preset", data={"preset": "role_delete"})
        [preset] = self.saved()["rules"]
        self.assertEqual((preset["events"], preset["limit"]), (["role_delete"], 3))
        events_before = len(pulse_db.events(limit=100))
        sim = self.client.post("/admin/security/antinuke/rules/simulate", data={"rule_id": preset["id"], "count": "3"})
        self.assertIn("würde auslösen", sim.text)
        self.assertIn("nur gemeldet", sim.text)
        self.assertEqual(len(pulse_db.events(limit=100)), events_before)  # a simulation stores nothing

    def test_stale_form_does_not_overwrite_a_newer_change(self):
        self.write({"enabled": True, "revision": 5})
        r = self.client.post("/admin/security/antinuke/rules/save", data={**self.rule_form(), "revision": "4"}, follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])
        self.assertNotIn("rules", self.saved())
        r = self.client.post("/admin/security/antinuke/rules/save", data={**self.rule_form(), "revision": "5"}, follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        self.assertEqual(self.saved()["revision"], 6)

    def global_form(self, **change):
        form = {"form_version": "2", "antinuke_enabled": "1", "antinuke_events": ["ban"], "action_limit": "3", "window_seconds": "10",
                "response": "ban", "mode": "alert", "rule_cooldown_seconds": "600", "action_cooldown_seconds": "600",
                "on_missing_permissions": "alert", "global_rule_enabled": "1"}
        form.update(change)
        return form

    def test_first_activation_is_alert_mode_and_protect_needs_confirmation(self):
        legacy_form = {"antinuke_enabled": "1", "antinuke_events": ["ban"], "action_limit": "3", "window_seconds": "10", "response": "ban"}
        self.client.post("/admin/security/antinuke", data=legacy_form)
        self.assertEqual(pm.normalize_antinuke(self.saved())["mode"], "alert")
        r = self.client.post("/admin/security/antinuke", data=self.global_form(mode="protect"), follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])
        self.assertEqual(self.saved()["mode"], "alert")
        r = self.client.post("/admin/security/antinuke", data=self.global_form(mode="protect", confirm_protect="1"), follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        self.assertEqual(self.saved()["mode"], "protect")
        r = self.client.post("/admin/security/antinuke", data=self.global_form(mode="protect", rule_cooldown_seconds="5"), follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])  # bounds are checked server-side

    def test_global_save_keeps_rules_trusted_and_invalid_entries(self):
        stored = {"enabled": True, "mode": "alert", "trusted_user_ids": [111111111111111111],
                  "rules": [rule("r1", ["ban"], 2, 10, ["log"]), {"id": "kaputt", "events": ["nix"]}]}
        self.write(stored)
        self.client.post("/admin/security/antinuke", data=self.global_form())
        saved = self.saved()
        self.assertEqual(saved["rules"], stored["rules"])  # untouched, also the invalid one
        self.assertEqual(saved["trusted_user_ids"], [111111111111111111])
        page = self.client.get("/admin/security")
        self.assertIn("ist ungültig", page.text)

    def test_trusted_check_and_save(self):
        check = self.client.post("/admin/security/antinuke/trusted/check",
                                 data={"trusted_user_ids": "abc\n111111111111111111", "trusted_role_ids": [str(ROLE.id)]})
        self.assertIn("keine gültige Discord-ID", check.text)
        self.assertIn("nicht (mehr) auf dem Server", check.text)
        self.assertIn("gefährliche Rechte", check.text)
        self.assertFalse(self.config_path.exists())  # checking stores nothing
        bad = self.client.post("/admin/security/antinuke/trusted", data={"trusted_user_ids": "abc"}, follow_redirects=False)
        self.assertIn("t=err", bad.headers["location"])
        self.client.post("/admin/security/antinuke/trusted", data={"trusted_user_ids": "111111111111111111", "trusted_role_ids": [str(ROLE.id)]})
        saved = self.saved()
        self.assertEqual((saved["trusted_user_ids"], saved["trusted_role_ids"]), ([111111111111111111], [ROLE.id]))
        self.assertTrue(any("hinzugefügt" in a[3] for a in self.audit if a[2] == "Anti-Nuke-Ausnahmen geändert"))

    def test_manual_lockdown_needs_confirmation_and_reason(self):
        self.write({"enabled": True, "lockdown_channel_ids": [GUILD.text_channels[0].id]})
        start = AsyncMock(return_value={"status": "ok", "detail": "1 Kanal gesperrt"})
        with patch("cogs.antinuke.start_lockdown", start):
            r = self.client.post("/admin/security/lockdown/start", data={"reason": "Raid läuft", "confirm": "0"}, follow_redirects=False)
            self.assertIn("t=err", r.headers["location"])
            r = self.client.post("/admin/security/lockdown/start", data={"reason": "x", "confirm": "1"}, follow_redirects=False)
            self.assertIn("t=err", r.headers["location"])
            start.assert_not_awaited()
            r = self.client.post("/admin/security/lockdown/start", data={"reason": "Raid läuft", "confirm": "1"}, follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        start.assert_awaited_once()
        self.assertEqual(pulse_db.events(event_type="Anti-Nuke: Lockdown gestartet")[0]["actor_id"], "123")

    def test_overview_shows_mode_permissions_and_hints(self):
        self.write({"enabled": True, "mode": "alert", "rules": [rule("r1", ["channel_delete"], 3, 10, ["log", "lockdown", "ban"])]})
        page = self.client.get("/admin/security").text
        self.assertIn("Alarmmodus", page)
        self.assertIn("KEINE automatischen Sanktionen", page)
        self.assertIn("keine Lockdown-Kanäle ausgewählt", page)
        self.assertIn("Mitglieder bannen", page)
        self.assertIn('name="antinuke_events" value="ban" checked>', page)  # legacy fields, defaults kept
        self.assertIn('name="antinuke_events" value="webhook_create">', page)


if __name__ == "__main__":
    unittest.main()
