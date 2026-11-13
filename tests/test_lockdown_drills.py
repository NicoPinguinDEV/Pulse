"""Audit #20/#21: timed lockdown expiry, second-person approval and side-effect-free security drills."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from fastapi.testclient import TestClient

import pulse_db
import pulse_lockdown_policy as ldp
import pulse_moderation as pm
import pulse_security_drills as drills
import webserver as ws
from cogs import antinuke
from cogs.antinuke import lift_expired_lockdown, load_lockdown, save_lockdown, start_lockdown
from tests.test_antinuke_control_center import FakeGuild
from tests.test_guard_admin import GUARD_GUILD, auth_with
from tests.test_pulse_admin_area import ADMIN

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


class Channel:
    def __init__(self, cid):
        self.id, self.name, self.type, self.ow = cid, f"k{cid}", "text", None
        self.set_permissions = AsyncMock(side_effect=self._set)

    def overwrites_for(self, _role):
        return discord.PermissionOverwrite() if self.ow is None else discord.PermissionOverwrite(**dict(self.ow))

    async def _set(self, _role, overwrite=None, reason=None):
        self.ow = overwrite


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
                        patch.object(antinuke, "LOCK_EDIT_PAUSE", 0)]
        for item in self.patches:
            item.start()
        pulse_db.init_db()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()


class PolicyTests(DbCase):
    def test_minutes_are_bounded_and_empty_means_no_expiry(self):
        for empty in (None, "", 0, "0"):
            self.assertIsNone(ldp.parse_minutes(empty))
        self.assertEqual(ldp.parse_minutes("30"), 30)
        for bad in ("4", "1441", "abc", "-5", "1.5"):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                ldp.parse_minutes(bad)

    def test_policy_defaults_keep_old_behaviour_and_garbage_is_ignored(self):
        self.assertEqual(ldp.load_policy(), {"require_approval": False, "default_minutes": None})
        self.assertEqual(ldp.normalize_policy({"require_approval": "yes", "default_minutes": 99999}),
                         {"require_approval": False, "default_minutes": None})
        saved = ldp.save_policy({"require_approval": True, "default_minutes": "60"})
        self.assertEqual(ldp.load_policy(), saved)
        self.assertEqual(saved, {"require_approval": True, "default_minutes": 60})

    def test_expiry_logic_fails_safe(self):
        active = {"active": True, "expires_at": (NOW - timedelta(minutes=1)).isoformat()}
        self.assertTrue(ldp.is_expired(active, NOW))
        self.assertFalse(ldp.is_expired({**active, "expires_at": (NOW + timedelta(minutes=1)).isoformat()}, NOW))
        for state in ({"active": True}, {"active": True, "expires_at": "kaputt"}, {"active": False, "expires_at": active["expires_at"]},
                      None, "x"):
            self.assertFalse(ldp.is_expired(state, NOW), state)

    def test_request_needs_a_different_person_and_expires(self):
        request = ldp.new_request("1", "Alice", "Raid läuft gerade", 30, NOW)
        ldp.check_approval(request, "2", NOW)
        with self.assertRaises(ValueError):
            ldp.check_approval(request, "1", NOW)
        with self.assertRaises(ValueError):
            ldp.check_approval(request, "2", NOW + timedelta(minutes=ldp.REQUEST_TTL_MINUTES, seconds=1))
        with self.assertRaises(ValueError):
            ldp.check_approval(None, "2", NOW)
        with self.assertRaises(ValueError):
            ldp.new_request("1", "Alice", "x", None, NOW)


class ExpiryTests(DbCase):
    def make_guild(self):
        guild = FakeGuild()
        guild.channels = {1: Channel(1), 2: Channel(2)}
        return guild

    def test_lockdown_with_duration_stores_expiry_and_lifts_itself(self):
        guild = self.make_guild()

        async def run():
            started = await start_lockdown(guild, [1, 2], reason="Test", by="Admin", minutes=30)
            state = load_lockdown()
            early = await lift_expired_lockdown(guild, datetime.now(timezone.utc))  # 30 minutes are not over yet
            return started, state, early
        started, state, early = asyncio.run(run())
        self.assertEqual(started["status"], "ok")
        self.assertIn("30 Minuten", started["detail"])
        self.assertTrue(state["expires_at"])
        self.assertIsNone(early)
        self.assertTrue(load_lockdown()["active"])

        later = datetime.fromisoformat(state["expires_at"]) + timedelta(seconds=1)
        result = asyncio.run(lift_expired_lockdown(guild, later))
        self.assertEqual(result["failed"], [])
        self.assertFalse(load_lockdown()["active"])
        self.assertTrue(all(ch.ow is None for ch in guild.channels.values()))

    def test_lockdown_without_duration_never_expires(self):
        guild = self.make_guild()
        asyncio.run(start_lockdown(guild, [1], reason="Test", by="Admin"))
        self.assertNotIn("expires_at", load_lockdown())
        self.assertIsNone(asyncio.run(lift_expired_lockdown(guild, NOW + timedelta(days=365))))
        self.assertTrue(load_lockdown()["active"])

    def test_failed_restore_is_retried_a_limited_number_of_times(self):
        guild = self.make_guild()
        asyncio.run(start_lockdown(guild, [1], reason="Test", by="Admin", minutes=5))
        later = datetime.fromisoformat(load_lockdown()["expires_at"]) + timedelta(minutes=1)
        guild.channels[1].set_permissions = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no"))
        for attempt in range(1, ldp.MAX_EXPIRY_ATTEMPTS + 1):
            result = asyncio.run(lift_expired_lockdown(guild, later))
            self.assertTrue(result["failed"])
            self.assertEqual(load_lockdown()["expiry_attempts"], attempt)
        calls = guild.channels[1].set_permissions.await_count
        result = asyncio.run(lift_expired_lockdown(guild, later))
        self.assertTrue(result.get("gave_up"))
        self.assertEqual(guild.channels[1].set_permissions.await_count, calls)  # no further Discord calls
        self.assertTrue(load_lockdown()["active"])  # stays active for a person

    def test_expired_but_already_lifted_state_is_ignored(self):
        save_lockdown({"active": False, "expires_at": (NOW - timedelta(days=1)).isoformat()})
        self.assertIsNone(asyncio.run(lift_expired_lockdown(self.make_guild(), NOW)))


class DrillTests(DbCase):
    def test_raid_drill_uses_its_own_watch_and_reports_every_level(self):
        report = drills.raid_drill(7, "1", "Alice")
        self.assertTrue(report["passed"], report["checks"])
        self.assertEqual(len(report["checks"]), 4)
        self.assertEqual(drills.recent_reports(7)[0]["id"], report["id"])

    def test_automod_drill_runs_samples_through_live_rules_without_side_effects(self):
        config = pm.normalize_automod({"enabled": True, "rules": ["invites", "mentions", "caps"], "mode": "test"})
        report = drills.automod_drill(7, "1", "Alice", config)
        self.assertTrue(report["passed"], report["checks"])
        broken = pm.normalize_automod({"enabled": True, "rules": ["mentions"], "mention_limit": 50})
        self.assertFalse(drills.automod_drill(7, "1", "Alice", broken)["passed"])  # 10 mentions stay below the limit
        off = drills.automod_drill(7, "1", "Alice", pm.normalize_automod({"enabled": False}))
        self.assertTrue(any(check["name"] == "AutoMod aktiv" and not check["ok"] and not check["required"] for check in off["checks"]))

    def test_lockdown_drill_is_read_only_and_flags_problems(self):
        guild = FakeGuild()
        guild.channels = {1: Channel(1)}
        ok = drills.lockdown_drill(guild, "1", "Alice", [1], {}, ldp.normalize_policy({}))
        self.assertTrue(ok["passed"], ok["checks"])
        self.assertIsNone(guild.channels[1].ow)
        guild.channels[1].set_permissions.assert_not_awaited()
        self.assertFalse(load_lockdown().get("active"))
        for channels, state, roles in (([], {}, True), ([99], {}, True), ([1], {"active": True}, True), ([1], {}, False)):
            guild = FakeGuild(perms={"roles": roles})
            guild.channels = {1: Channel(1)}
            self.assertFalse(drills.lockdown_drill(guild, "1", "A", channels, state, {})["passed"], (channels, state, roles))

    def test_reports_are_guild_scoped_bounded_and_part_of_the_backup(self):
        drills.raid_drill(7, "1", "Alice")
        drills.raid_drill(8, "2", "Bob")
        self.assertEqual(len(drills.recent_reports(7)), 1)
        self.assertEqual(set(drills.last_by_kind(7)), {"raid"})
        state = pulse_db.export_state()
        self.assertEqual(len(state["security_drills"]), 2)
        pulse_db.import_state(state)
        self.assertEqual(len(drills.recent_reports(8)), 1)
        with patch.object(drills, "MAX_REPORTS_PER_GUILD", 2):
            for _ in range(4):
                drills.raid_drill(7, "1", "Alice")
        self.assertEqual(len(drills.recent_reports(7, 100)), 2)


class AdminRouteTests(DbCase):
    def setUp(self):
        super().setUp()
        self.config_path = Path(self.tmp.name) / "config.json"
        self.config_path.write_text(json.dumps({"pulse_antinuke": {"enabled": True, "lockdown_channel_ids": [GUARD_GUILD.text_channels[0].id]}}), encoding="utf-8")
        self.audit = []
        self.user_id = "123"

        def auth(request, session, perm="can_view_dashboard", admin=False):
            ctx = auth_with(ADMIN)(request, session, perm, admin)
            ctx.user = {**ctx.user, "id": self.user_id}
            return ctx
        self.extra = [patch.object(ws, "CONFIG_FILE", str(self.config_path)), patch.object(ws, "auth", auth),
                      patch.object(ws, "log_audit", lambda *a, **k: self.audit.append(a))]
        for item in self.extra:
            item.start()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for item in reversed(self.extra):
            item.stop()
        super().tearDown()

    def post(self, path, **data):
        return self.client.post(path, data=data, follow_redirects=False)

    def test_start_with_duration_passes_minutes_and_rejects_bad_values_before_acting(self):
        start = AsyncMock(return_value={"status": "ok", "detail": "1 Kanal gesperrt"})
        with patch("cogs.antinuke.start_lockdown", start):
            bad = self.post("/admin/security/lockdown/start", reason="Raid läuft", confirm="1", minutes="2")
            self.assertIn("t=err", bad.headers["location"])
            start.assert_not_awaited()
            self.post("/admin/security/lockdown/start", reason="Raid läuft", confirm="1", minutes="45")
        self.assertEqual(start.await_args.kwargs["minutes"], 45)

    def test_default_duration_applies_when_the_form_leaves_it_empty(self):
        ldp.save_policy({"default_minutes": 20})
        start = AsyncMock(return_value={"status": "ok", "detail": "x"})
        with patch("cogs.antinuke.start_lockdown", start):
            self.post("/admin/security/lockdown/start", reason="Raid läuft", confirm="1", minutes="")
        self.assertEqual(start.await_args.kwargs["minutes"], 20)

    def test_with_approval_policy_nothing_starts_until_a_different_admin_approves(self):
        ldp.save_policy({"require_approval": True})
        start = AsyncMock(return_value={"status": "ok", "detail": "1 Kanal gesperrt"})
        with patch("cogs.antinuke.start_lockdown", start):
            requested = self.post("/admin/security/lockdown/start", reason="Raid läuft", confirm="1", minutes="30")
            self.assertIn("t=ok", requested.headers["location"])
            start.assert_not_awaited()
            pending = ldp.load_request()
            self.assertEqual((pending["by_id"], pending["minutes"]), ("123", 30))
            self_approve = self.post("/admin/security/lockdown/approve", request_id=pending["id"])
            self.assertIn("t=err", self_approve.headers["location"])
            start.assert_not_awaited()
            self.user_id = "456"
            wrong = self.post("/admin/security/lockdown/approve", request_id="LDR-000000")
            self.assertIn("t=err", wrong.headers["location"])
            approved = self.post("/admin/security/lockdown/approve", request_id=pending["id"])
            self.assertIn("t=ok", approved.headers["location"])
            again = self.post("/admin/security/lockdown/approve", request_id=pending["id"])
            self.assertIn("t=err", again.headers["location"])
        start.assert_awaited_once()
        self.assertEqual(start.await_args.kwargs["minutes"], 30)
        self.assertIsNone(ldp.load_request())
        self.assertTrue(any("freigegeben von" in str(a[3]) for a in self.audit))

    def test_expired_or_cancelled_request_cannot_be_approved(self):
        ldp.save_policy({"require_approval": True})
        self.post("/admin/security/lockdown/start", reason="Raid läuft", confirm="1")
        pending = ldp.load_request()
        self.user_id = "456"
        self.assertIn("t=ok", self.post("/admin/security/lockdown/cancel", request_id=pending["id"]).headers["location"])
        start = AsyncMock(return_value={"status": "ok", "detail": "x"})
        with patch("cogs.antinuke.start_lockdown", start):
            self.assertIn("t=err", self.post("/admin/security/lockdown/approve", request_id=pending["id"]).headers["location"])
        start.assert_not_awaited()
        ldp.save_request({**pending, "valid_until": (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()})
        self.assertIsNone(ldp.load_request())

    def test_policy_route_validates_and_audits(self):
        bad = self.post("/admin/security/lockdown/policy", default_minutes="3")
        self.assertIn("t=err", bad.headers["location"])
        self.assertEqual(ldp.load_policy()["default_minutes"], None)
        self.post("/admin/security/lockdown/policy", default_minutes="90", require_approval="1")
        self.assertEqual(ldp.load_policy(), {"require_approval": True, "default_minutes": 90})
        self.assertTrue(any(a[2] == "Anti-Nuke: Lockdown-Richtlinie geändert" for a in self.audit))

    def test_drill_route_runs_and_reports_without_touching_the_lockdown(self):
        for kind in ("raid", "automod", "lockdown"):
            response = self.post("/admin/security/drills/run", kind=kind)
            self.assertEqual(response.status_code, 303, kind)
        self.assertEqual({r["kind"] for r in drills.recent_reports(GUARD_GUILD.id)}, {"raid", "automod", "lockdown"})
        self.assertFalse(load_lockdown().get("active"))
        self.assertIn("t=err", self.post("/admin/security/drills/run", kind="x").headers["location"])
        self.assertEqual(len(drills.recent_reports(GUARD_GUILD.id)), 3)

    def test_page_shows_policy_request_and_drill_sections(self):
        ldp.save_policy({"require_approval": True})
        self.post("/admin/security/lockdown/start", reason="Raid läuft", confirm="1")
        page = self.client.get("/admin/security").text
        self.assertIn("Offene Lockdown-Anfrage", page)
        self.assertIn("Sicherheitsübungen", page)
        self.assertIn("noch nie geübt", page)


if __name__ == "__main__":
    unittest.main()
