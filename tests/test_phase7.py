"""Phase 7 (F-01 … F-10): switches default off, read-only checks, maintenance mode, history, evaluations.

Real FastAPI routes with a fake login, temp database and fake Discord objects – no network.
"""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord
from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_access
import pulse_board
import pulse_db
import pulse_flags
import pulse_insights
import pulse_jobs
import pulse_notify_prefs
import pulse_phase7
import pulse_public_status as ps
import pulse_settings_history as sh
import webserver as ws
from tests.fakes import FakeChannel, FakeGuild, FakeMember, FakeRole
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

UTC = timezone.utc
FLAG_PAGES = {"rights_check": "/admin/rights", "visibility_check": "/admin/visibility", "settings_history": "/admin/settings-history",
              "capacity_heatmap": "/team/capacity", "promotion_readiness": "/team/readiness",
              "application_board": "/applications/board", "my_month": "/me/month"}


class PermRole:
    """Hashable role stand-in with real discord.Permissions (overwrite dict keys must be hashable)."""

    def __init__(self, rid, permissions):
        self.id, self.permissions = rid, permissions


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.root = root
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(ws, "CONFIG_FILE", str(root / "config.json")),
                        patch.object(ws, "APPS_FILE", str(root / "applications.json")),
                        patch.object(ws, "DATA_FILE", str(root / "team_data.json")),
                        patch.object(ws, "SHIFTS_FILE", str(root / "shifts.json"))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        pulse_flags.invalidate()
        ps.reset_cache()
        sh.clear_actor()

    def tearDown(self):
        sh.clear_actor()
        for p in reversed(self.patches):
            p.stop()
        pulse_flags.invalidate()
        self.tmp.cleanup()


class SwitchTests(TempDb):
    def test_everything_is_off_by_default_and_pages_answer_404(self):
        self.assertFalse(any(pulse_flags.states().values()))
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            for path in FLAG_PAGES.values():
                self.assertEqual(client.get(path).status_code, 404, path)
            self.assertEqual(client.get("/admin/features").status_code, 200)
        self.assertEqual(client.get("/status").status_code, 404)

    def test_switch_on_shows_pages_and_rights_still_apply(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        guild = FakeGuild()
        guild.default_role = PermRole(1, discord.Permissions.none())
        admin = fake_auth_for(ADMIN)

        def admin_ctx(*a, **k):
            ctx = admin(*a, **k)
            ctx.guild = guild
            return ctx
        with patch.object(ws, "auth", admin_ctx):
            for key in FLAG_PAGES:
                r = client.post("/admin/features/toggle", data={"key": key, "on": "1"}, follow_redirects=False)
                self.assertIn("t=ok", r.headers["location"])
            for path in FLAG_PAGES.values():
                self.assertEqual(client.get(path).status_code, 200, path)
            self.assertIn("t=err", client.post("/admin/features/toggle", data={"key": "nope", "on": "1"},
                                               follow_redirects=False).headers["location"])
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            for path in ("/admin/rights", "/admin/visibility", "/admin/settings-history", "/team/capacity",
                         "/team/readiness", "/applications/board", "/admin/features"):
                self.assertEqual(client.get(path).status_code, 403, path)
            self.assertEqual(client.post("/admin/features/toggle", data={"key": "my_month", "on": "0"}).status_code, 403)
        self.assertTrue(pulse_flags.enabled("my_month"))

    def test_sections_only_for_switched_on_functions(self):
        keys = [s[0] for s in pulse_phase7.sections(ADMIN)]
        self.assertEqual(keys, ["admin-features"])
        pulse_flags.set_enabled("capacity_heatmap", True)
        self.assertIn("team-capacity", [s[0] for s in pulse_phase7.sections(dict(TEAMLER, can_view_analytics=True))])
        self.assertEqual(pulse_phase7.sections(TEAMLER), [])


class MaintenanceTests(TempDb):
    def test_blocks_only_writes_of_non_admins_while_active(self):
        self.assertFalse(pulse_flags.blocks_write("POST", "/tickets/x", False))
        pulse_flags.set_enabled("maintenance", True)
        pulse_flags.set_maintenance(True, "Backup", "Admin")
        self.assertTrue(pulse_flags.blocks_write("POST", "/tickets/x", False))
        self.assertFalse(pulse_flags.blocks_write("GET", "/tickets", False))
        self.assertFalse(pulse_flags.blocks_write("POST", "/tickets/x", True))
        self.assertFalse(pulse_flags.blocks_write("POST", "/logout", False))
        self.assertIn("Backup", pulse_flags.banner_html(ws.esc))
        pulse_flags.set_enabled("maintenance", False)  # switching the function off ends the mode
        self.assertFalse(pulse_flags.maintenance()["active"])
        self.assertEqual(pulse_flags.banner_html(ws.esc), "")

    def test_real_auth_refuses_writes_with_503(self):
        pulse_flags.set_enabled("maintenance", True)
        pulse_flags.set_maintenance(True, "", "Admin")
        guild = FakeGuild()
        bot = SimpleNamespace(get_guild=lambda _: guild)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=bot)), method="POST",
                                  url=SimpleNamespace(path="/tasks/create"), query_params={})
        perms = {k: True for k in ws.PERM_KEYS}
        with patch.object(ws, "get_current_user", lambda _: {"id": "5", "username": "x"}), \
                patch.object(ws, "compute_perms", lambda *a: (dict(perms, is_admin=False), None)):
            with self.assertRaises(HTTPException) as caught:
                ws.auth(request, "cookie")
            self.assertEqual(caught.exception.status_code, 503)
            request.method = "GET"
            self.assertEqual(ws.auth(request, "cookie").user["id"], "5")
        with patch.object(ws, "get_current_user", lambda _: {"id": "5", "username": "x"}), \
                patch.object(ws, "compute_perms", lambda *a: (dict(perms, is_admin=True), None)):
            request.method = "POST"
            self.assertTrue(ws.auth(request, "cookie").perms["is_admin"])

    def test_automations_pause_but_live_support_keeps_running(self):
        calls = []

        @pulse_jobs.tracked("Test-Job", 60)
        async def job():
            calls.append("job")

        @pulse_jobs.tracked("Test-Voice", 8, pausable=False)
        async def voice():
            calls.append("voice")
        pulse_flags.set_enabled("maintenance", True)
        pulse_flags.set_maintenance(True, "", "Admin")
        asyncio.run(job())
        asyncio.run(voice())
        self.assertEqual(calls, ["voice"])
        pulse_flags.set_maintenance(False, "", "Admin")
        asyncio.run(job())
        self.assertEqual(calls, ["voice", "job"])


class HistoryTests(TempDb):
    def test_records_only_dashboard_changes_while_switched_on(self):
        pulse_db.set_setting("weekly", {"a": 1})
        sh.set_actor("7", "Lea")
        pulse_db.set_setting("weekly", {"a": 2})  # switch off → nothing
        self.assertEqual(sh.entries(), [])
        pulse_flags.set_enabled("settings_history", True)
        pulse_db.set_setting("weekly", {"a": 3})
        sh.clear_actor()
        pulse_db.set_setting("weekly", {"a": 4})  # bot write, no person → nothing
        rows = sh.entries()
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["actor_name"], json.loads(rows[0]["before_json"]), json.loads(rows[0]["after_json"])),
                         ("Lea", {"a": 2}, {"a": 3}))
        self.assertIn(("-", '  "a": 2'), sh.diff_lines(rows[0]["before_json"], rows[0]["after_json"]))

    def test_config_diff_secret_redaction_and_revert(self):
        ws.save_json(ws.CONFIG_FILE, {"weekly_goal_hours": 5, "team_role_ids": [1]})
        pulse_flags.set_enabled("settings_history", True)
        sh.set_actor("7", "Lea")
        ws.save_json(ws.CONFIG_FILE, {"weekly_goal_hours": 8, "team_role_ids": [1], "api_token": "geheim"})
        rows = {r["key"]: r for r in sh.entries()}
        self.assertEqual(set(rows), {"weekly_goal_hours", "api_token"})
        self.assertNotIn("geheim", json.dumps(rows["api_token"]))
        self.assertFalse(rows["api_token"]["revertible"])
        with self.assertRaises(ValueError):
            sh.revert(rows["api_token"]["id"], load_config=ws.load_config, save_config=lambda c: ws.save_json(ws.CONFIG_FILE, c))
        sh.revert(rows["weekly_goal_hours"]["id"], load_config=lambda: ws.load_json(ws.CONFIG_FILE, {}),
                  save_config=lambda c: ws.save_json(ws.CONFIG_FILE, c))
        self.assertEqual(ws.load_json(ws.CONFIG_FILE, {})["weekly_goal_hours"], 5)
        self.assertEqual(sh.entries()[0]["key"], "weekly_goal_hours")  # the revert itself is recorded

    def test_history_is_bounded(self):
        pulse_flags.set_enabled("settings_history", True)
        sh.set_actor("7", "Lea")
        with patch.object(sh, "MAX_ROWS", 5):
            for i in range(9):
                pulse_db.set_setting("k", i)
        self.assertEqual(len(sh.entries()), 5)


class AccessTests(unittest.TestCase):
    def test_matrix_and_simulation_use_the_permission_model(self):
        lead, sup = FakeRole(10, "Leitung", 5), FakeRole(11, "Support", 2)
        guild = FakeGuild(roles=[lead, sup], members=[FakeMember(1, "Anna", [lead]), FakeMember(2, "Ben", [sup]),
                                                       FakeMember(3, "Bot", [sup], bot=True)])
        config = {"team_role_ids": [10, 11], "permissions": {"10": {"can_view_dashboard": True, "can_promote": True}}}
        rows = {r["member"].display_name: r for r in pulse_access.matrix(guild, config)}
        self.assertEqual(set(rows), {"Anna", "Ben"})
        self.assertIn("can_manage_tickets", rows["Anna"]["grants"])  # inherited from can_promote
        self.assertEqual(list(rows["Ben"]["grants"]), ["can_view_dashboard"])  # never configured → E-4
        sim = pulse_access.simulate(guild, guild.get_member(2), [10], config)
        self.assertIn("can_promote", sim["grants"])
        self.assertEqual(guild.get_member(2).roles, [sup])  # nothing changed

    def test_channel_visibility_follows_discord_overwrites(self):
        everyone = PermRole(1, discord.Permissions(view_channel=True))
        unverified = PermRole(5, discord.Permissions.none())
        open_ch = SimpleNamespace(id=100, name="regeln", position=1, overwrites={})
        hidden = SimpleNamespace(id=101, name="team", position=2,
                                 overwrites={everyone: discord.PermissionOverwrite(view_channel=False)})
        unverified_denied = SimpleNamespace(id=102, name="rp", position=3,
                                            overwrites={unverified: discord.PermissionOverwrite(view_channel=False)})
        leak = SimpleNamespace(id=103, name="intern", position=4,
                               overwrites={everyone: discord.PermissionOverwrite(view_channel=False),
                                           unverified: discord.PermissionOverwrite(view_channel=True)})
        guild = SimpleNamespace(default_role=everyone, channels=[open_ch, hidden, unverified_denied, leak],
                                get_role=lambda rid: unverified if rid == 5 else None)
        result = pulse_access.visibility(guild, 5, expected_ids={100})
        self.assertEqual([c.name for c in result["visible"]], ["regeln", "intern"])
        self.assertEqual([c.name for c in result["warnings"]], ["intern"])


class InsightTests(unittest.TestCase):
    NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)

    def test_heatmap_marks_hours_without_duty(self):
        monday_10 = datetime(2026, 10, 19, 8, 30, tzinfo=UTC)  # 10:30 Berlin
        tickets = [{"opened_at": (monday_10 - timedelta(days=7 * w)).isoformat()} for w in range(4)]
        shifts = [{"mod_id": "1", "started_at_iso": datetime(2026, 10, 20, 12, 0, tzinfo=UTC).isoformat(),
                   "ended_at_iso": datetime(2026, 10, 20, 14, 0, tzinfo=UTC).isoformat()}]
        data = pulse_insights.heatmap(tickets, [], shifts, now=self.NOW, days=28)
        self.assertEqual(data["demand"][0][10], 1.0)
        self.assertEqual(data["staff"][1][14], 0.25)  # 1 h in 4 weeks, Tuesday 14 Berlin
        self.assertEqual(data["hints"][0]["kind"], "unbesetzt")

    def test_readiness_is_only_a_hint(self):
        criteria = pulse_insights.normalize_criteria({})["default"]
        stats = pulse_insights.member_stats(
            "1", now=self.NOW,
            shift_history=[{"mod_id": "1", "started_at_iso": "2026-10-10T10:00:00+02:00", "duration_seconds": 12 * 3600}],
            tickets=[{"claimed_by_id": "1", "status": "closed", "closed_at": "2026-10-11T10:00:00+00:00", "rating": 5}] * 5,
            attempts=[{"user_id": "1", "training_id": "t", "passed": 1}],
            entry={"team_since": "2026-08-01T10:00:00+02:00"}, active_warns=[])
        result = pulse_insights.readiness(stats, criteria)
        self.assertTrue(result["ready"], result)
        stats["probation_active"] = True
        self.assertFalse(pulse_insights.readiness(stats, criteria)["ready"])

    def test_month_review(self):
        start, end = pulse_insights.month_bounds(self.NOW, previous=True)
        self.assertEqual((start.month, end.month), (9, 10))
        review = pulse_insights.month_review(
            "1", start=start, end=end,
            shift_history=[{"mod_id": "1", "started_at_iso": "2026-09-03T10:00:00+02:00", "duration_seconds": 7200},
                           {"mod_id": "1", "started_at_iso": "2026-10-03T10:00:00+02:00", "duration_seconds": 7200}],
            tickets=[{"claimed_by_id": "1", "status": "closed", "closed_at": "2026-09-05T10:00:00+00:00", "rating": 4}],
            attempts=[], unlocked=[{"name": "Erste Schicht", "unlocked_at": "2026-09-03T12:00:00+00:00"}])
        self.assertEqual((review["label"], review["hours"], review["tickets"], review["achievements"]),
                         ("September 2026", 2.0, 1, ["Erste Schicht"]))
        self.assertIn("September 2026", pulse_insights.month_text(review))


class MonthDmTests(TempDb):
    def test_queued_once_per_month_only_for_opt_in(self):
        pulse_notify_prefs._dm_queue.clear()
        pulse_flags.set_enabled("my_month", True)
        pulse_db.set_setting(pulse_phase7.MONTH_OPTIN_KEY, {"42": True})
        first_of_month = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
        self.assertEqual(pulse_phase7.queue_month_reviews(ws, datetime(2026, 11, 10, tzinfo=UTC)), 0)  # too late in the month
        self.assertEqual(pulse_phase7.queue_month_reviews(ws, first_of_month), 1)
        self.assertEqual(pulse_phase7.queue_month_reviews(ws, first_of_month), 0)  # once
        self.assertEqual(pulse_notify_prefs._dm_queue[0][0], "42")
        pulse_notify_prefs._dm_queue.clear()


class BoardTests(unittest.TestCase):
    def test_stages_come_from_existing_fields(self):
        now = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
        apps = {
            "a": {"status": "pending", "name": "A", "created_at": "10.10.2026 10:00"},
            "b": {"status": "pending", "name": "B", "created_at": "20.10.2026 10:00", "upvotes": ["1"]},
            "c": {"status": "interview", "name": "C", "interview_invited_at": "19.10.2026 10:00"},
            "d": {"status": "hired", "name": "D", "user_id": "9", "decided_at": "15.10.2026 10:00"},
            "e": {"status": "rejected", "name": "E", "decided_at": "01.01.2026 10:00"},
        }
        cols = pulse_board.board(apps, {"9": {"probation": {"active": True, "started_at": "2026-10-15T10:00:00+02:00"}}},
                                 pulse_board.normalize_deadlines({}), now)
        self.assertEqual([c["id"] for c in cols["eingang"]], ["a"])
        self.assertTrue(cols["eingang"][0]["overdue"])
        self.assertEqual([c["id"] for c in cols["pruefung"]], ["b"])
        self.assertEqual([c["id"] for c in cols["gespraech"]], ["c"])
        self.assertEqual([c["id"] for c in cols["probezeit"]], ["d"])
        self.assertEqual(cols["abgelehnt"], [])  # older than 30 days → history view only


class PublicStatusTests(TempDb):
    def test_snapshot_has_no_personal_data(self):
        data = ps.snapshot(SimpleNamespace(name="G"), voice_cfg={"active": True},
                           open_tickets=[{"user_name": "Max", "user_id": "1", "claimed_by_id": None},
                                         {"user_name": "Eva", "user_id": "2", "claimed_by_id": "7"}],
                           voice_waiting=3, rp_status={"state": "stop", "next_start": "14:30"}, now=datetime(2026, 10, 21, 10, tzinfo=UTC))
        self.assertEqual((data["queue_tickets"], data["queue_voice"], data["rp_state"]), (1, 3, "stop"))
        self.assertNotIn("Max", json.dumps(data))

    def test_page_off_on_and_game_status(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        guild = FakeGuild(channels=[FakeChannel(1)])
        previous = getattr(ws.app.state, "bot", None)
        ws.app.state.bot = SimpleNamespace(get_guild=lambda _: guild)
        try:
            self.assertEqual(client.get("/status.json").status_code, 404)
            pulse_flags.set_enabled("public_status", True)
            pulse_db.set_setting("rp_status", {"state": "start"})
            data = client.get("/status.json").json()
            self.assertEqual((data["rp_state"], data["game"]), ("start", None))  # F-09 off → no request
            self.assertIn("Roleplay", client.get("/status").text)
        finally:
            ws.app.state.bot = previous

        class Client:
            async def get(self, url, params):
                self.url, self.params = url, params
                return SimpleNamespace(status_code=200, json=lambda: {"data": [{"name": "Bochum", "playing": 12, "maxPlayers": 40}]})
        fake = Client()
        pulse_flags.set_enabled("game_status", True)
        with patch.dict("os.environ", {"PULSE_ROBLOX_UNIVERSE_ID": "123"}):
            game = asyncio.run(ps.game_status(lambda: fake, now=1000.0))
        self.assertEqual((game["playing"], fake.params), (12, {"universeIds": "123"}))
        self.assertIsNone(ps.parse_game({"data": []}))


if __name__ == "__main__":
    unittest.main()
