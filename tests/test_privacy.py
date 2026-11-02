"""D-08: visibility of the team file (E-3), privacy notice, retention and data export."""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

TARGET = SimpleNamespace(id=555, bot=False, display_name="Ziel", roles=[], avatar=None, nick=None,
                         joined_at=None, status="offline", top_role=None, guild_permissions=SimpleNamespace(administrator=False))


def guild_with_target():
    return SimpleNamespace(id=1, name="G", members=[TARGET], roles=[], channels=[], text_channels=[], member_count=1,
                           owner_id=0, icon=None, get_member=lambda uid: TARGET if uid == TARGET.id else None,
                           get_role=lambda _: None, get_channel=lambda _: None)


class TeamFileVisibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
                        patch.object(ws, "DATA_FILE", str(base / "team_data.json")),
                        patch.object(ws, "SHIFTS_FILE", str(base / "shifts.json")),
                        patch.object(ws, "log_audit", lambda *a, **k: None)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        ws.save_json(ws.DATA_FILE, {"555": {"warns_list": [{"id": "w1", "reason": "Geheimer Warngrund", "active": True,
                                                              "date": "20.10.2026 10:00", "expires_at": "2099-01-01T00:00:00+00:00"}],
                                            "notes": ["Interne Notiz XYZ"]}})
        self.client = TestClient(ws.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def page(self, perms, user_id="123"):
        base = fake_auth_for(perms)

        def auth(*a, **k):
            ctx = base(*a, **k)
            ctx.guild = guild_with_target()
            ctx.user = {"id": user_id, "global_name": "Tester"}
            return ctx
        with patch.object(ws, "auth", auth):
            response = self.client.get(f"/member/{TARGET.id}")
        self.assertEqual(response.status_code, 200, response.text[-500:])
        return response.text

    def test_plain_teamler_sees_neither_warnings_nor_notes(self):
        html = self.page(TEAMLER)
        self.assertNotIn("Geheimer Warngrund", html)
        self.assertNotIn("Interne Notiz XYZ", html)
        self.assertIn("Nur für die Person selbst", html)

    def test_person_sees_own_warnings_but_not_notes(self):
        html = self.page(TEAMLER, user_id=str(TARGET.id))
        self.assertIn("Geheimer Warngrund", html)
        self.assertNotIn("Interne Notiz XYZ", html)

    def test_warn_and_note_permissions(self):
        html = self.page(dict(TEAMLER, can_warn=True))
        self.assertIn("Geheimer Warngrund", html)
        self.assertNotIn("Interne Notiz XYZ", html)
        html = self.page(dict(TEAMLER, can_add_notes=True))
        self.assertNotIn("Geheimer Warngrund", html)
        self.assertIn("Interne Notiz XYZ", html)
        html = self.page(ADMIN)
        self.assertIn("Geheimer Warngrund", html)
        self.assertIn("Interne Notiz XYZ", html)


class WarningLeakTests(unittest.TestCase):
    """E-3 also applies to search, team lists and the Suite file – not only /member."""

    def setUp(self):
        from cogs import pulse_suite
        self.suite = pulse_suite
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
                        patch.object(ws, "DATA_FILE", str(base / "team_data.json")),
                        patch.object(ws, "APPS_FILE", str(base / "applications.json")),
                        patch.object(ws, "LOGS_FILE", str(base / "logs.json")),
                        patch.object(pulse_suite, "DB_PATH", str(base / "suite.db"))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        pulse_suite.init_db()
        ws.save_json(ws.DATA_FILE, {"555": {"warns_list": [{"id": "w1", "reason": "Geheimer Warngrund"}]}})
        ws.save_json(ws.LOGS_FILE, [{
            "id": "ml1", "target_user": "RobloxGeheim", "roblox_id": "987654321",
            "type": "Ban", "reason": "MelonlyGeheim",
        }])

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def search(self, perms, user_id="123"):
        ctx = SimpleNamespace(guild=guild_with_target(), perms=perms, user={"id": user_id})
        return [r for r in self.suite.search_all("geheim", ctx) if r["type"] == "Warnung"]

    def test_search_hides_other_peoples_warnings(self):
        self.assertEqual(self.search(TEAMLER), [])
        self.assertEqual(len(self.search(TEAMLER, user_id="555")), 1)  # own warning
        self.assertEqual(len(self.search(dict(TEAMLER, can_warn=True))), 1)

    def melonly_search(self, perms, user_id="123"):
        ctx = SimpleNamespace(guild=guild_with_target(), perms=perms, user={"id": user_id})
        return [r for r in self.suite.search_all("melonlygeheim", ctx) if r["type"] == "Roblox/MOD"]

    def test_search_hides_melonly_entries_without_melonly_access(self):
        self.assertEqual(self.melonly_search(TEAMLER), [])
        self.assertEqual(len(self.melonly_search(dict(TEAMLER, can_warn=True))), 1)
        self.assertEqual(len(self.melonly_search(dict(TEAMLER, can_moderate_bans=True))), 1)
        self.assertEqual(len(self.melonly_search(ADMIN)), 1)

    def test_roblox_apis_require_melonly_access(self):
        ctx = SimpleNamespace(perms=dict(TEAMLER), user={"id": "123"})
        client = TestClient(ws.app)
        with patch.object(ws, "auth", return_value=ctx) as authenticate:
            suggestions = client.get("/api/roblox-search?query=RobloxGeheim")
            lookup = client.get("/api/roblox-user?username=RobloxGeheim")
        self.assertEqual(suggestions.status_code, 403)
        self.assertEqual(lookup.status_code, 403)
        self.assertEqual(authenticate.call_count, 2)

    def test_application_search_is_restricted_to_recruiting_roles(self):
        from cogs import pulse_suite
        ws.save_json(ws.APPS_FILE, {
            "app_secret": {
                "user_id": "555", "name": "RecruitingSecretName", "status": "pending",
                "text": "RecruitingSecretMotivation",
            }
        })
        with pulse_suite.cx() as con:
            con.execute(
                "INSERT INTO suite_interviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("int_secret", "app_secret", "555", "InterviewSecretName", "{}", 90,
                 "Very suitable", "InterviewSecretNotes", "123", "Reviewer", "2026-11-02T10:00:00"),
            )
        viewer = SimpleNamespace(guild=guild_with_target(), perms=dict(TEAMLER), user={"id": "123"})
        self.assertFalse(any(x["type"] in {"Bewerbung", "Gespräch"}
                             for x in pulse_suite.search_all("RecruitingSecret", viewer)))
        self.assertFalse(any(x["type"] == "Gespräch"
                             for x in pulse_suite.search_all("InterviewSecret", viewer)))

        recruiter_perms = dict(TEAMLER, can_manage_applications=True)
        recruiter = SimpleNamespace(guild=guild_with_target(), perms=recruiter_perms, user={"id": "123"})
        self.assertTrue(any(x["type"] == "Bewerbung"
                            for x in pulse_suite.search_all("RecruitingSecret", recruiter)))
        self.assertTrue(any(x["type"] == "Gespräch"
                            for x in pulse_suite.search_all("InterviewSecret", recruiter)))

    def test_application_read_permission_helper_is_explicit(self):
        self.assertFalse(ws.can_review_applications(SimpleNamespace(perms=dict(TEAMLER))))
        for key in ("can_manage_applications", "can_promote", "is_admin"):
            self.assertTrue(ws.can_review_applications(SimpleNamespace(perms={key: True})), key)

    def test_melonly_read_access_helper_matches_existing_permission_set(self):
        self.assertFalse(ws.can_access_melonly(SimpleNamespace(perms=dict(TEAMLER))))
        for key in ("can_warn", "can_moderate_kicks", "can_moderate_bans",
                    "can_moderate_bolo", "can_add_notes", "is_admin"):
            self.assertTrue(ws.can_access_melonly(SimpleNamespace(perms={key: True})), key)

    def test_helpers(self):
        self.assertFalse(ws.can_see_warnings(TEAMLER, "1", "2"))
        self.assertTrue(ws.can_see_warnings(TEAMLER, "2", 2))
        self.assertTrue(ws.can_see_warnings(dict(TEAMLER, can_warn=True), "1", "2"))
        self.assertFalse(ws.can_see_notes(TEAMLER))
        self.assertTrue(ws.can_see_notes(dict(TEAMLER, can_promote=True)))


class RetentionTests(unittest.TestCase):
    def setUp(self):
        import pulse_backup
        self.pulse_backup = pulse_backup
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
                        patch.object(ws, "APPS_FILE", str(base / "applications.json")),
                        patch.object(ws, "AUDIT_FILE", str(base / "audit_logs.json")),
                        patch.object(ws, "DATA_FILE", str(base / "team_data.json")),
                        patch.object(ws, "SHIFTS_FILE", str(base / "shifts.json")),
                        patch.object(ws, "DB_ABMELDUNGEN", str(base / "abmeldungen.db")),
                        patch.object(ws, "BACKUP_DIR", str(base / "backups")),
                        patch.object(ws, "BASE_DIR", base), patch.object(ws, "DATA_DIR", base)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        ws.save_json(ws.APPS_FILE, {
            "alt_abgelehnt": {"user_id": "1", "status": "rejected", "created_at": "01.01.2025 10:00", "decided_at": "02.01.2025 10:00"},
            "alt_offen": {"user_id": "2", "status": "pending", "created_at": "01.01.2025 10:00"},
            "neu": {"user_id": "3", "status": "hired", "created_at": "01.10.2026 10:00", "decided_at": "05.10.2026 10:00"},
        })
        ws.save_json(ws.AUDIT_FILE, [{"timestamp": "01.01.2023 10:00:00", "actor_id": "1", "action": "alt"},
                                     {"timestamp": "01.10.2026 10:00:00", "actor_id": "1", "action": "neu"}])
        self.now = __import__("datetime").datetime(2026, 10, 20, tzinfo=__import__("datetime").timezone.utc)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_off_by_default_and_needs_fresh_backup(self):
        import pulse_retention
        self.assertFalse(pulse_retention.load_settings()["enabled"])
        self.assertFalse(pulse_retention.run(ws, self.now)["ran"])
        self.assertEqual(pulse_retention.plan(ws, now=self.now), {"applications": 1, "audit": 1})
        pulse_retention.save_settings({"enabled": True})
        with patch.object(ws, "log_audit", lambda *a, **k: None):
            report = pulse_retention.run(ws, self.now)
        self.assertFalse(report["ran"])
        self.assertIn("Komplett-Backup", report["reason"])
        self.assertEqual(len(ws.load_json(ws.APPS_FILE, {})), 3)

    def test_cleanup_keeps_open_and_recent_and_is_logged(self):
        import pulse_retention
        pulse_retention.save_settings({"enabled": True})
        self.pulse_backup.create_full_backup(ws.DATA_DIR, ws.full_backup_dir())
        report = pulse_retention.run(ws, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        self.assertTrue(report["ran"])
        self.assertEqual(sorted(ws.load_json(ws.APPS_FILE, {})), ["alt_offen", "neu"])
        actions = [e["action"] for e in ws.load_json(ws.AUDIT_FILE, [])]
        self.assertNotIn("alt", actions)
        self.assertIn("Datenbereinigung (Aufbewahrungsfristen)", actions)
        self.assertFalse(pulse_retention.due_today())

    def test_hired_applications_are_kept_for_team_since(self):
        import pulse_retention
        apps = {"eingestellt": {"status": "hired", "decided_at": "01.01.2020 10:00"},
                "abgelehnt": {"status": "rejected", "decided_at": "01.01.2020 10:00"}}
        self.assertEqual(pulse_retention.expired_applications(apps, 183, self.now), ["abgelehnt"])

    def test_failure_is_recorded_once_per_day(self):
        import pulse_retention
        pulse_retention.save_settings({"enabled": True})
        self.pulse_backup.create_full_backup(ws.DATA_DIR, ws.full_backup_dir())
        with patch.object(ws, "save_json", side_effect=ws.JsonWriteBlocked("gesperrt")):
            report = pulse_retention.run(ws, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        self.assertFalse(report["ran"])
        self.assertIn("fehlgeschlagen", report["reason"])
        self.assertFalse(pulse_retention.due_today())

    def test_export_covers_all_databases(self):
        import json as _json
        import sqlite3
        import pulse_retention
        con = sqlite3.connect(Path(ws.DATA_DIR) / "pulse_suite.db")
        con.execute("CREATE TABLE suite_members(user_id TEXT, notes TEXT)")
        con.execute("INSERT INTO suite_members VALUES ('100000000000000001','Notiz')")
        con.commit()
        con.close()
        data = pulse_retention.export_person(ws, "100000000000000001")
        self.assertEqual(data["databases"]["pulse_suite.db"]["suite_members"][0]["notes"], "Notiz")
        _json.dumps(data, default=str)

    def test_export_and_admin_routes(self):
        import json as _json
        client = TestClient(ws.app)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(client.get("/admin/privacy").status_code, 403)
            self.assertEqual(client.get("/admin/privacy/export?discord_id=100000000000000001").status_code, 403)
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            privacy_page = client.get("/admin/privacy")
            self.assertIn("Aufbewahrungsfristen", privacy_page.text)
            self.assertIn("Dashboard-Anmeldeprotokoll", privacy_page.text)
            self.assertIn("IP-Adresse", privacy_page.text)
            self.assertIn("Notfall-Backup-Server", privacy_page.text)
            saved = client.post("/admin/privacy", data={"enabled": "1", "applications_days": "183", "audit_days": "730",
                                                        "contact": "Leitung per Ticket"}, follow_redirects=False)
            self.assertIn("t=ok", saved.headers["location"])
            ws.save_json(ws.APPS_FILE, {"a": {"user_id": "100000000000000001", "status": "pending"}})
            export = client.get("/admin/privacy/export?discord_id=100000000000000001")
        self.assertEqual(export.status_code, 200)
        self.assertIn("a", _json.loads(export.text)["applications"])
        self.assertIn("dashboard_login_ip_audit", _json.loads(export.text))
        import pulse_retention
        self.assertIn("Leitung per Ticket", pulse_retention.privacy_notice_html(ws.esc))


if __name__ == "__main__":
    unittest.main()
