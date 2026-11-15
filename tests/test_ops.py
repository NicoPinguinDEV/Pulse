"""Audit #153/#155/#157/#158/#165/#169/#172: operations console and analytics (temp database, fake admin login, no Discord)."""
import logging
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import pulse_ops as ops
import webserver as ws

UTC = timezone.utc
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
ADMIN = {"can_view_dashboard": True, "is_admin": True}
LEAD = {"can_view_dashboard": True, "can_promote": True}


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.db = str(root / "pulse.db")
        self.patches = [patch.object(pulse_db, "DB_PATH", self.db), patch.object(ws, "AUDIT_FILE", str(root / "audit.json")),
                        patch.object(ws, "APPS_FILE", str(root / "apps.json"))]
        for p in self.patches:
            p.start()
        pulse_db._SCHEMA_READY.discard(self.db)
        pulse_db.init_db()
        ops.LATENCY.reset()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        pulse_db._SCHEMA_READY.discard(self.db)
        self.tmp.cleanup()


class ErrorGroupCase(TempDb):
    def test_signature_ignores_ids_numbers_paths_and_values(self):
        a = ops.signature("tickets", "Ticket 123 in C:\\data\\x.db nicht gefunden 'abc' id=deadbeef01")
        b = ops.signature("tickets", "Ticket 9 in /opt/pulse/y.db nicht gefunden 'zzz' id=cafebabe99")
        self.assertEqual(a, b)
        self.assertNotEqual(a, ops.signature("tickets", "Etwas ganz anderes"))
        self.assertNotEqual(a, ops.signature("voice", "Ticket 123 in C:\\data\\x.db nicht gefunden 'abc' id=deadbeef01"))

    def test_occurrences_are_counted_in_one_group(self):
        for i in range(3):
            ops.record_error("tickets", f"Ticket {i} nicht gefunden", NOW + timedelta(minutes=i))
        ops.record_error("voice", "Kanal weg", NOW)
        groups = {g["source"]: g for g in ops.error_groups(10, NOW + timedelta(minutes=5))}
        self.assertEqual(groups["tickets"]["count"], 3)
        self.assertEqual(groups["tickets"]["first_seen"], NOW.isoformat(timespec="seconds"))
        self.assertTrue(groups["tickets"]["recent"] and groups["tickets"]["new"])
        self.assertFalse(ops.error_groups(10, NOW + timedelta(days=3))[0]["new"])

    def test_groups_are_bounded_to_the_newest(self):
        with patch.object(ops, "MAX_GROUPS", 3):
            for i in range(6):
                ops.record_error(f"quelle{i}", "Fehler", NOW + timedelta(minutes=i))
            self.assertEqual([g["source"] for g in ops.error_groups(10, NOW)], ["quelle5", "quelle4", "quelle3"])

    def test_logging_handler_feeds_groups_once_and_is_idempotent(self):
        logger = logging.getLogger("pulse.testops")
        handler = ops.install_error_handler("pulse")
        self.assertIs(ops.install_error_handler("pulse"), handler)
        try:
            logger.error("Zeitüberschreitung bei Anfrage %d", 7)
            logger.warning("nur eine Warnung")
        finally:
            logging.getLogger("pulse").removeHandler(handler)
        groups = ops.error_groups(10)
        self.assertEqual(len(groups), 1)
        self.assertEqual((groups[0]["source"], groups[0]["count"]), ("testops", 1))

    def test_storage_failure_never_raises(self):
        with patch.object(pulse_db, "connect", side_effect=RuntimeError("disk")):
            ops.record_error("x", "y")


class LatencyCase(unittest.TestCase):
    def setUp(self):
        ops.LATENCY.reset()

    def test_percentiles_errors_and_ordering(self):
        for ms in range(1, 101):
            ops.LATENCY.record("GET /a", ms, 200)
        ops.LATENCY.record("GET /a", 500, 500)
        ops.LATENCY.record("GET /b", 5, 200)
        rows = {r["route"]: r for r in ops.LATENCY.snapshot()}
        self.assertEqual(rows["GET /a"]["errors"], 1)
        self.assertEqual(rows["GET /a"]["count"], 101)
        self.assertEqual(rows["GET /a"]["max"], 500)
        self.assertGreaterEqual(rows["GET /a"]["p95"], rows["GET /a"]["p50"])
        self.assertEqual(ops.LATENCY.snapshot()[0]["route"], "GET /a")  # slowest p95 first

    def test_routes_and_samples_are_bounded(self):
        with patch.object(ops, "MAX_ROUTES", 2):
            for r in ("a", "b", "c"):
                ops.LATENCY.record(r, 1, 200)
        self.assertEqual({r["route"] for r in ops.LATENCY.snapshot()}, {"a", "b"})
        for _ in range(ops.SAMPLES + 50):
            ops.LATENCY.record("a", 1, 200)
        self.assertEqual(ops.LATENCY._routes["a"]["count"], ops.SAMPLES + 51)
        self.assertEqual(len(ops.LATENCY._routes["a"]["ms"]), ops.SAMPLES)

    def test_middleware_records_route_templates_not_raw_paths(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        client.get("/favicon.ico")
        client.get("/definitiv-nicht-vorhanden-123")
        routes = {r["route"] for r in ops.LATENCY.snapshot()}
        self.assertFalse(any("definitiv-nicht-vorhanden" in r for r in routes))
        self.assertTrue(any("(unbekannt)" in r for r in routes))


class ResourceCase(TempDb):
    def test_sampling_is_throttled_stored_and_thinned(self):
        first = ops.sample_resources(self.tmp.name, NOW)
        self.assertIsNotNone(first)
        self.assertIsNone(ops.sample_resources(self.tmp.name, NOW + timedelta(minutes=1)))
        self.assertIsNotNone(ops.sample_resources(self.tmp.name, NOW + timedelta(minutes=6)))
        later = NOW + timedelta(days=ops.KEEP_DAYS + 1)
        ops.sample_resources(self.tmp.name, later)
        self.assertEqual([r["at"] for r in ops.resource_series(24 * 14, later + timedelta(minutes=1))], [later.isoformat(timespec="seconds")])
        self.assertIsNotNone(first["disk_free_mb"])

    def test_summary_and_trend(self):
        series = [{"container_mb": v, "process_mb": None, "cpu_pct": 1.0, "disk_free_mb": 900.0} for v in (100, 100, 100, 200, 200, 200)]
        s = ops.resource_summary(series)
        self.assertEqual((s["container_mb"]["min"], s["container_mb"]["max"], s["container_mb"]["trend"]), (100, 200, "steigend"))
        self.assertIsNone(s["process_mb"])
        self.assertEqual(s["cpu_pct"]["trend"], "stabil")
        self.assertEqual(ops.resource_summary([])["container_mb"], None)

    def test_never_raises_on_storage_failure(self):
        with patch.object(pulse_db, "connect", side_effect=RuntimeError("disk")):
            self.assertIsNone(ops.sample_resources(self.tmp.name, NOW, force=True))


class ActionsAndRetentionCase(TempDb):
    def test_admin_actions_merge_sources_sort_and_filter(self):
        ws.save_json(ws.AUDIT_FILE, [{"id": "a1", "actor": "Ann", "actor_id": "1", "action": "Rolle geändert", "details": "x", "timestamp": "10.11.2026 09:00:00"},
                                     {"id": "a2", "actor": "Bo", "actor_id": "2", "action": "Backup erstellt", "details": "", "timestamp": "12.11.2026 09:00:00"}])
        with pulse_db.connect() as db:
            import pulse_settings_history as sh
            sh._ensure(db)
            db.execute("INSERT INTO settings_history (at,actor_id,actor_name,source,key,revertible) VALUES (?,?,?,?,?,1)", ("2026-11-11T10:00:00+00:00", "3", "Cy", "dashboard", "weekly_goal"))
        rows = ops.admin_actions(ws)
        self.assertEqual([r["actor"] for r in rows], ["Bo", "Cy", "Ann"])
        self.assertEqual([r["actor"] for r in ops.admin_actions(ws, source="einstellung")], ["Cy"])
        self.assertEqual([r["actor"] for r in ops.admin_actions(ws, query="backup")], ["Bo"])
        self.assertEqual(ops.admin_actions(ws, limit=1)[0]["actor"], "Bo")

    def test_admin_actions_survive_broken_audit_file(self):
        with patch.object(ws, "load_json", side_effect=OSError("kaputt")):
            self.assertIsInstance(ops.admin_actions(ws), list)

    def test_retention_matrix_lists_every_store_with_counts_and_isolates_failures(self):
        ws.save_json(ws.APPS_FILE, {"a": {"created_at": "01.01.2026 10:00"}, "b": {"created_at": "05.02.2026 10:00"}})
        rows = {r["key"]: r for r in ops.retention_matrix(ws)}
        self.assertEqual(set(rows), {"applications", "audit", "tickets", "notifications", "tasks", "mod_cases", "wiki_versions", "error_groups", "resources"})
        self.assertEqual(rows["applications"]["count"], 2)
        self.assertEqual(rows["applications"]["oldest"], "01.01.2026"[:10])
        self.assertFalse(rows["applications"]["automatic"])  # retention mutations are locked in this release
        self.assertTrue(rows["error_groups"]["automatic"])
        with patch.object(pulse_db, "connect", side_effect=RuntimeError("x")):
            broken = {r["key"]: r for r in ops.retention_matrix(ws)}
        self.assertEqual(broken["tickets"]["error"], "nicht lesbar")
        self.assertIsNone(broken["tickets"]["count"])
        self.assertEqual(broken["applications"]["count"], 2)

    def test_new_tables_are_in_the_backup_or_documented(self):
        ops.record_error("x", "y")
        state = pulse_db.export_state()
        self.assertEqual(len(state["ops_error_groups"]), 1)


class ConsoleRouteCase(TempDb):
    @staticmethod
    def as_user(perms):
        def auth(request, session, perm="can_view_dashboard", admin=False):
            if admin and not perms.get("is_admin"):
                raise HTTPException(status_code=403, detail="nur Admin")
            return SimpleNamespace(user={"id": "1", "global_name": "A"}, guild=SimpleNamespace(id=1, name="G", members=[]), guild_name="G",
                                   perms=perms, config={}, member=None, msg="", msg_ok=True)
        return patch.object(ws, "auth", auth)

    def test_every_view_renders_for_admins(self):
        import pulse_ops_pages as pages
        ops.record_error("tickets", "Fehler 5")
        ops.LATENCY.record("GET /x", 12, 200)
        ops.sample_resources(self.tmp.name, datetime.now(UTC), force=True)
        client = TestClient(ws.app, raise_server_exceptions=False)
        with self.as_user(ADMIN):
            for key, _label in pages.VIEWS:
                resp = client.get(f"/admin/betrieb?ansicht={key}")
                self.assertEqual(resp.status_code, 200, key)
            self.assertIn("Fehler 5", client.get("/admin/betrieb?ansicht=fehler").text)
            self.assertIn("GET /x", client.get("/admin/betrieb?ansicht=latenz").text)
            self.assertEqual(client.get("/admin/betrieb?ansicht=ressourcen&h=abc").status_code, 200)
            self.assertEqual(client.get("/admin/betrieb?ansicht=nope").status_code, 200)
            escaped = client.get("/admin/betrieb?ansicht=aktionen&q=<script>x</script>").text
            self.assertNotIn("<script>x</script>", escaped)

    def test_non_admins_are_denied(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with self.as_user(LEAD):
            self.assertEqual(client.get("/admin/betrieb").status_code, 403)

    def test_sparkline_handles_short_and_flat_series(self):
        import pulse_ops_pages as pages
        self.assertIn("Noch zu wenige", pages._spark([]))
        self.assertIn("polyline", pages._spark([5, 5, 5]))
        self.assertIn("polyline", pages._spark([1, None, 3]))


if __name__ == "__main__":
    unittest.main()


class RightsDiffCase(unittest.TestCase):
    def guild(self):
        from tests.fakes import FakeGuild, FakeMember, FakeRole
        support, plain, admin_role, everyone = FakeRole(10, "Support"), FakeRole(11, "Gast"), FakeRole(12, "Chef"), FakeRole(1, "@everyone")
        admin_role.permissions = SimpleNamespace(administrator=True, value=8)
        everyone.permissions = SimpleNamespace(administrator=False, value=0)
        boss = FakeMember(5, "Boss", roles=[admin_role], admin=True)
        owner = FakeMember(6, "Inhaber", admin=True)
        guild = FakeGuild(members=[boss, owner], roles=[support, plain, admin_role, everyone], owner_id=6)
        guild.default_role = everyone
        return guild

    def test_findings_are_classified_and_sorted(self):
        import pulse_rights_diff as rd
        config = {"team_role_ids": [10, 99], "permissions": {"10": {"can_warn": True}, "11": {"can_warn": True}, "77": {"can_warn": True}}}
        found = rd.diff(self.guild(), config)
        by_key = {f["key"]: f for f in found}
        self.assertEqual(by_key["missing_roles"]["items"], ["77 (Rechte-Eintrag)", "99 (Teamrolle)"])
        self.assertEqual(by_key["stale_rights"]["items"], ["Gast"])
        self.assertEqual(by_key["admin_roles"]["items"], ["Chef"])
        self.assertEqual(by_key["admin_members"]["items"], ["Boss"])  # the owner is not listed
        self.assertNotIn("unconfigured", by_key)  # role 10 has an entry
        order = {"bad": 0, "warn": 1, "info": 2}
        ranks = [order[f["severity"]] for f in found]
        self.assertEqual(ranks, sorted(ranks))

    def test_unconfigured_team_role_and_garbage_config(self):
        import pulse_rights_diff as rd
        found = {f["key"]: f for f in rd.diff(self.guild(), {"team_role_ids": [10]})}
        self.assertEqual(found["unconfigured"]["items"], ["Support"])
        for bad in (None, "x", {"team_role_ids": "abc", "permissions": [1, 2]}, {}):
            self.assertIsInstance(rd.diff(self.guild(), bad), list)

    def test_console_tab_renders_and_changes_nothing(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        guild = self.guild()

        def auth(request, session, perm="can_view_dashboard", admin=False):
            return SimpleNamespace(user={"id": "1", "global_name": "A"}, guild=guild, guild_name="G", perms=ADMIN, config={"team_role_ids": [10]},
                                   member=None, msg="", msg_ok=True)
        before = (list(guild.roles), [list(m.roles) for m in guild.members])
        with patch.object(ws, "auth", auth):
            resp = client.get("/admin/betrieb?ansicht=rechte")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Chef", resp.text)
        self.assertEqual(before, (list(guild.roles), [list(m.roles) for m in guild.members]))
        self.assertFalse(any(m.added_roles or m.removed_roles for m in guild.members))
