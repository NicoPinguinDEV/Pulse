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
        pulse_suite.audit(1, "123", "Reviewer", "app_secret", "RecruitingSecretName",
                          "application_updated", "RecruitingSecretAuditNotes")
        with pulse_suite.cx() as con:
            con.execute(
                "INSERT INTO suite_events VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("event_secret", "1", "Bewerbungsgespräch RecruitingSecretCalendarName", "event",
                 "2026-11-03T10:00:00", "2026-11-03T10:30:00",
                 "RecruitingSecretCalendarNotes", "555", "123", "Reviewer", "2026-11-02T10:00:00"),
            )
        viewer = SimpleNamespace(guild=guild_with_target(), perms=dict(TEAMLER), user={"id": "123"})
        self.assertFalse(any(x["type"] in {"Bewerbung", "Gespräch"}
                             for x in pulse_suite.search_all("RecruitingSecret", viewer)))
        self.assertFalse(any(x["type"] == "Gespräch"
                             for x in pulse_suite.search_all("InterviewSecret", viewer)))
        self.assertFalse(any(x["type"] == "Audit"
                             for x in pulse_suite.search_all("RecruitingSecretAuditNotes", viewer)))
        self.assertFalse(any(x["type"] == "Kalender"
                             for x in pulse_suite.search_all("RecruitingSecretCalendarNotes", viewer)))

        recruiter_perms = dict(TEAMLER, can_manage_applications=True)
        recruiter = SimpleNamespace(guild=guild_with_target(), perms=recruiter_perms, user={"id": "123"})
        self.assertTrue(any(x["type"] == "Bewerbung"
                            for x in pulse_suite.search_all("RecruitingSecret", recruiter)))
        self.assertTrue(any(x["type"] == "Gespräch"
                            for x in pulse_suite.search_all("InterviewSecret", recruiter)))
        self.assertTrue(any(x["type"] == "Audit"
                            for x in pulse_suite.search_all("RecruitingSecretAuditNotes", recruiter)))
        self.assertTrue(any(x["type"] == "Kalender"
                            for x in pulse_suite.search_all("RecruitingSecretCalendarNotes", recruiter)))

    def test_application_read_permission_helper_is_explicit(self):
        self.assertFalse(ws.can_review_applications(SimpleNamespace(perms=dict(TEAMLER))))
        for key in ("can_manage_applications", "can_promote", "is_admin"):
            self.assertTrue(ws.can_review_applications(SimpleNamespace(perms={key: True})), key)

    def test_application_page_redacts_sensitive_answers_for_dashboard_only_users(self):
        ws.save_json(ws.APPS_FILE, {
            "app_private": {
                "user_id": "987654321", "name": "BewerberExample", "status": "pending",
                "text": "PrivateMotivationAnswer",
                "career_label": "High-Team",
                "test_percent": 100, "test_score": 12, "test_total": 12,
                "test_details": [{"question": "PrivateQuestion", "selected_answer": "PrivateTestAnswer",
                                  "correct_answer": "PrivateCorrectAnswer", "correct": True}],
                "practical_answers": [{"title": "PrivateTask", "answer": "PrivatePracticalAnswer",
                                       "prompt": "PrivatePrompt", "review_guide": "PrivateReviewGuide"}],
                "reviews": {"777": {"notes": "PrivateInternalReview", "ratings": {"teamwork": 5}}},
            }
        })
        client = TestClient(ws.app)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            response = client.get("/applications")
        self.assertEqual(response.status_code, 200, response.text[-800:])
        self.assertIn("BewerberExample", response.text)  # limited identity needed for the existing vote flow
        for secret in ("PrivateMotivationAnswer", "PrivateQuestion", "PrivateTestAnswer",
                       "PrivateCorrectAnswer", "PrivatePracticalAnswer", "PrivateReviewGuide",
                       "PrivateInternalReview", "987654321"):
            self.assertNotIn(secret, response.text)
        self.assertIn("nur für Recruiting-Berechtigte sichtbar", response.text)

        recruiter = dict(TEAMLER, can_manage_applications=True)
        with patch.object(ws, "auth", fake_auth_for(recruiter)):
            reviewed = client.get("/applications")
        self.assertEqual(reviewed.status_code, 200, reviewed.text[-800:])
        for secret in ("PrivateMotivationAnswer", "PrivateQuestion", "PrivatePracticalAnswer",
                       "987654321"):
            self.assertIn(secret, reviewed.text)

    def test_suite_application_and_interview_pages_require_recruiting_access(self):
        from cogs import pulse_suite
        pulse_suite.register_routes()
        client = TestClient(ws.app)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            for url in ("/suite/applications", "/suite/applications/app_private", "/suite/interviews"):
                response = client.get(url)
                self.assertEqual(response.status_code, 403, url)

    def test_application_json_file_is_restricted_to_service_account(self):
        import os
        if os.name != "posix":
            self.skipTest("POSIX-Dateirechte werden nur auf Serverbetriebssystemen geprüft.")
        ws.save_json(ws.APPS_FILE, {"sensitive": {"text": "answer"}})
        os.chmod(ws.APPS_FILE, 0o644)
        ws.secure_application_storage()
        self.assertEqual(Path(ws.APPS_FILE).stat().st_mode & 0o777, 0o600)
        ws.save_json(ws.APPS_FILE, {"sensitive": {"text": "updated"}})
        self.assertEqual(Path(ws.APPS_FILE).stat().st_mode & 0o777, 0o600)

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
        self.assertIn("referenzsichere Phase 15.6e gesperrt", report["reason"])
        self.assertEqual(len(ws.load_json(ws.APPS_FILE, {})), 3)

    def test_cleanup_is_fail_closed_until_linked_recovery_is_approved(self):
        import pulse_retention
        pulse_retention.save_settings({"enabled": True})
        self.pulse_backup.create_full_backup(ws.DATA_DIR, ws.full_backup_dir())
        before_apps = Path(ws.APPS_FILE).read_bytes()
        before_audit = Path(ws.AUDIT_FILE).read_bytes()
        report = pulse_retention.run(ws, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        self.assertFalse(report["ran"])
        self.assertIn("referenzsichere Phase 15.6e gesperrt", report["reason"])
        self.assertEqual(Path(ws.APPS_FILE).read_bytes(), before_apps)
        self.assertEqual(Path(ws.AUDIT_FILE).read_bytes(), before_audit)
        self.assertEqual(sorted(ws.load_json(ws.APPS_FILE, {})), ["alt_abgelehnt", "alt_offen", "neu"])
        self.assertFalse(pulse_retention.due_today())

    def test_hired_applications_are_kept_for_team_since(self):
        import pulse_retention
        apps = {"eingestellt": {"status": "hired", "decided_at": "01.01.2020 10:00"},
                "abgelehnt": {"status": "rejected", "decided_at": "01.01.2020 10:00"}}
        self.assertEqual(pulse_retention.expired_applications(apps, 183, self.now), ["abgelehnt"])

    def test_retention_lock_does_not_attempt_json_mutations(self):
        import pulse_retention
        pulse_retention.save_settings({"enabled": True})
        self.pulse_backup.create_full_backup(ws.DATA_DIR, ws.full_backup_dir())
        with patch.object(ws, "save_json", side_effect=ws.JsonWriteBlocked("gesperrt")) as writer:
            report = pulse_retention.run(ws, __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        self.assertFalse(report["ran"])
        self.assertIn("referenzsichere Phase 15.6e gesperrt", report["reason"])
        writer.assert_not_called()
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

    def test_expired_applications_protect_in_review_and_unknown_statuses(self):
        import pulse_retention
        apps = {
            "rejected": {"status": "rejected", "decided_at": "01.01.2020 10:00"},
            "in_review": {"status": "in_review", "decided_at": "01.01.2020 10:00"},
            "unknown": {"status": "legacy_custom_state", "decided_at": "01.01.2020 10:00"},
            "missing": {"decided_at": "01.01.2020 10:00"},
            "accepted": {"status": "accepted", "decided_at": "01.01.2020 10:00"},
            "hired": {"status": "hired", "decided_at": "01.01.2020 10:00"},
        }
        self.assertEqual(
            pulse_retention.expired_applications(apps, 183, self.now),
            ["rejected"],
        )

    def test_export_includes_interviews_calendar_and_explicit_audit_references(self):
        import sqlite3
        import json as _json
        import pulse_retention

        applicant_id = "100000000000000011"
        interviewer_id = "100000000000000022"
        app_id = "app_export_subject"
        event_id = "event_export_subject"
        audit_ref = "audit_export_subject"
        ws.save_json(ws.APPS_FILE, {
            app_id: {
                "user_id": applicant_id, "status": "rejected",
                "interview_event_id": event_id,
                "history": [{"action": "interview_reviewed", "audit_ref": audit_ref}],
            }
        })
        ws.save_json(ws.DATA_FILE, {
            applicant_id: {"employment_event": {"audit_ref": audit_ref}}
        })
        ws.save_json(ws.AUDIT_FILE, [
            {"id": audit_ref, "actor_id": "900", "action": "Recruiting", "details": "Explicit reference"},
            {"id": "legacy-unlinked", "actor_id": "901", "action": "Recruiting", "details": applicant_id},
        ])

        suite_path = Path(ws.DATA_DIR) / "pulse_suite.db"
        with sqlite3.connect(suite_path) as con:
            con.execute(
                "CREATE TABLE suite_interviews("
                "id TEXT PRIMARY KEY, application_id TEXT, applicant_id TEXT, applicant_name TEXT, "
                "scores_json TEXT, overall INTEGER, recommendation TEXT, notes TEXT, "
                "interviewer_id TEXT, interviewer_name TEXT, created_at TEXT)"
            )
            con.execute(
                "INSERT INTO suite_interviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("interview-linked", app_id, "different-legacy-id", "Synthetic Applicant", "{}",
                 90, "Geeignet", "Synthetic interview notes", interviewer_id, "Synthetic Reviewer",
                 "2026-11-02T10:00:00"),
            )
            con.execute(
                "CREATE TABLE suite_audit("
                "id TEXT PRIMARY KEY, guild_id TEXT, occurred_at TEXT, actor_id TEXT, actor_name TEXT, "
                "target_id TEXT, target_name TEXT, action TEXT, details TEXT, request_id TEXT, prev_hash TEXT, hash TEXT)"
            )
            con.execute(
                "INSERT INTO suite_audit VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                ("suite-audit-linked", "1", "2026-11-02T10:00:00", "900", "Reviewer",
                 app_id, "Synthetic Applicant", "application_reviewed", "Synthetic detail", "req-1", "GENESIS", "hash"),
            )
        with sqlite3.connect(Path(ws.DATA_DIR) / "pulse.db") as con:
            con.execute(
                "INSERT INTO calendar_events(id,title,starts_at,created_by_id,created_by_name,created_at) "
                "VALUES(?,?,?,?,?,?)",
                (event_id, "Synthetic interview", "2026-11-05T10:00:00Z", "900", "Reviewer", "2026-11-02T10:00:00"),
            )

        applicant_export = pulse_retention.export_person(ws, applicant_id)
        suite_rows = applicant_export["databases"]["pulse_suite.db"]
        self.assertEqual(suite_rows["suite_interviews"][0]["notes"], "Synthetic interview notes")
        self.assertEqual(suite_rows["suite_audit"][0]["id"], "suite-audit-linked")
        self.assertEqual(
            applicant_export["databases"]["pulse.db"]["calendar_events"][0]["id"],
            event_id,
        )
        self.assertEqual(
            applicant_export["audit_as_explicit_application_reference"][0]["id"],
            audit_ref,
        )
        self.assertNotIn("legacy-unlinked", {
            entry["id"] for entry in applicant_export["audit_as_explicit_application_reference"]
        })

        interviewer_export = pulse_retention.export_person(ws, interviewer_id)
        self.assertEqual(interviewer_export["applications"], {})
        self.assertEqual(
            interviewer_export["databases"]["pulse_suite.db"]["suite_interviews"][0]["interviewer_id"],
            interviewer_id,
        )
        _json.dumps(applicant_export, default=str)

    def test_export_reports_sqlite_table_truncation(self):
        import sqlite3
        import pulse_retention
        suite_path = Path(ws.DATA_DIR) / "pulse_suite.db"
        with sqlite3.connect(suite_path) as con:
            con.execute("CREATE TABLE suite_members(user_id TEXT, notes TEXT)")
            con.executemany(
                "INSERT INTO suite_members VALUES(?,?)",
                [("100000000000000033", "one"), ("100000000000000033", "two")],
            )
        with patch.object(pulse_retention, "EXPORT_ROW_LIMIT", 1):
            exported = pulse_retention.export_person(ws, "100000000000000033")
        self.assertEqual(len(exported["databases"]["pulse_suite.db"]["suite_members"]), 1)
        self.assertIn("suite_members", exported["database_export_truncated"]["pulse_suite.db"])

    def test_linked_retention_preview_is_read_only_and_never_authorizes_deletion(self):
        import sqlite3
        import pulse_retention
        old = __import__("datetime").datetime(2026, 10, 20, tzinfo=__import__("datetime").timezone.utc)
        ws.save_json(ws.APPS_FILE, {
            "old_rejected": {
                "user_id": "100000000000000044", "status": "rejected",
                "decided_at": "01.01.2025 10:00", "interview_event_id": "evt-linked",
            }
        })
        suite_path = Path(ws.DATA_DIR) / "pulse_suite.db"
        with sqlite3.connect(suite_path) as con:
            con.execute("CREATE TABLE suite_interviews(id TEXT PRIMARY KEY, application_id TEXT, applicant_id TEXT)")
            con.execute("INSERT INTO suite_interviews VALUES(?,?,?)", ("int-linked", "old_rejected", "100000000000000044"))
            con.execute("CREATE TABLE suite_audit(id TEXT PRIMARY KEY, target_id TEXT)")
            con.execute("INSERT INTO suite_audit VALUES(?,?)", ("aud-linked", "old_rejected"))
        with sqlite3.connect(Path(ws.DATA_DIR) / "pulse.db") as con:
            con.execute(
                "INSERT INTO calendar_events(id,title,starts_at,created_by_id,created_by_name,created_at) "
                "VALUES(?,?,?,?,?,?)",
                ("evt-linked", "Synthetic", "2025-01-02T10:00:00Z", "0", "System", "2025-01-01"),
            )
        before_apps = Path(ws.APPS_FILE).read_bytes()
        preview = pulse_retention.plan_linked_retention(
            ws, settings={"applications_days": 183, "audit_days": 730}, now=old
        )
        self.assertTrue(preview["read_only"])
        self.assertFalse(preview["deletion_performed"])
        self.assertFalse(preview["deletion_supported"])
        self.assertFalse(preview["deletion_allowed"])
        self.assertEqual(preview["candidate_applications"], 1)
        self.assertEqual(preview["linked_interview_rows"], 1)
        self.assertEqual(preview["linked_calendar_events"], 1)
        self.assertEqual(preview["linked_suite_audit_rows"], 1)
        self.assertIn("multi_store_apply_and_recovery_not_implemented", preview["blockers"])
        self.assertEqual(Path(ws.APPS_FILE).read_bytes(), before_apps)

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
        self.assertIn(
            "Eine automatische Löschung abgeschlossener Bewerbungen ist derzeit nicht aktiviert",
            pulse_retention.privacy_notice_html(ws.esc),
        )
        self.assertNotIn(
            "wird deine Bewerbung nach etwa 6 Monaten automatisch gelöscht",
            pulse_retention.privacy_notice_html(ws.esc),
        )


if __name__ == "__main__":
    unittest.main()
