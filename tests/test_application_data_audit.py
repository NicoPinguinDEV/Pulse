"""Tests for the read-only, privacy-safe Phase 15.6 application audit."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from application_data_audit import audit_application_data  # noqa: E402


class ApplicationDataAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.suite_db = self.base / "pulse_suite.db"
        self.pulse_db = self.base / "pulse.db"

        with sqlite3.connect(self.suite_db) as con:
            con.execute(
                "CREATE TABLE suite_interviews("
                "id TEXT PRIMARY KEY, application_id TEXT, applicant_id TEXT, applicant_name TEXT, "
                "scores_json TEXT, overall INTEGER, recommendation TEXT, notes TEXT, "
                "interviewer_id TEXT, interviewer_name TEXT, created_at TEXT)"
            )
            con.execute(
                "INSERT INTO suite_interviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("int_private", "hired_app", "111111111111111111", "Private Applicant", "{}", 90,
                 "Geeignet", "Private interview notes", "222222222222222222", "Reviewer", "2026-11-02T10:00:00"),
            )
            con.execute(
                "INSERT INTO suite_interviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("int_orphan", "deleted_app", "333333333333333333", "Other Private Applicant", "{}", 80,
                 "Geeignet", "Other private notes", "222222222222222222", "Reviewer", "2026-11-02T11:00:00"),
            )

        with sqlite3.connect(self.pulse_db) as con:
            con.execute(
                "CREATE TABLE calendar_events("
                "id TEXT PRIMARY KEY, title TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT, "
                "category TEXT, location TEXT, description TEXT, created_by_id TEXT, "
                "created_by_name TEXT, created_at TEXT)"
            )
            con.execute(
                "INSERT INTO calendar_events VALUES(?,?,?,?,?,?,?,?,?,?)",
                ("evt_private", "Private interview", "2026-11-05T10:00:00Z", None, "event", "Discord",
                 "Private application appointment", "222222222222222222", "Reviewer", "2026-11-02T10:00:00"),
            )

        self.applications = {
            "hired_app": {
                "user_id": "111111111111111111",
                "status": "hired",
                "team_since": None,
                "interview_id": "int_private",
                "interview_event_id": "evt_private",
            },
            "legacy_accepted": {
                "user_id": "444444444444444444",
                "status": "accepted",
                "interview_id": "missing_interview",
                "interview_event_id": "missing_event",
            },
            "future_status": {
                "user_id": "555555555555555555",
                "status": "private-status-value-should-not-leak",
                "interview_id": "int_orphan",
            },
            "missing_user": {
                "status": "pending",
            },
            "invalid_record": "not-an-object",
        }
        self.team_data = {
            "111111111111111111": {"team_since": "2026-10-01T10:00:00+02:00"},
        }

    def tearDown(self):
        self.tmp.cleanup()

    def test_reports_only_counts_for_employment_and_cross_store_references(self):
        report = audit_application_data(
            self.applications, self.team_data, self.suite_db, self.pulse_db
        )

        self.assertTrue(report["read_only"])
        self.assertFalse(report["personal_data_emitted"])
        self.assertEqual(report["applications"]["records"], 5)
        self.assertEqual(report["applications"]["valid_object_records"], 4)
        self.assertEqual(report["applications"]["non_object_records"], 1)
        self.assertEqual(report["applications"]["unknown_or_missing_states"], 1)
        self.assertEqual(report["applications"]["missing_user_ids"], 1)
        self.assertEqual(report["employment"]["accepted_or_hired_records"], 2)
        self.assertEqual(report["employment"]["records_without_team_since"], 1)
        self.assertEqual(report["interviews"]["rows"], 2)
        self.assertEqual(report["interviews"]["orphan_application_refs"], 1)
        self.assertEqual(report["interviews"]["application_interview_references"], 3)
        self.assertEqual(report["interviews"]["application_refs_without_matching_interview"], 1)
        self.assertEqual(report["interviews"]["application_interview_id_mismatches"], 1)
        self.assertEqual(report["calendar"]["application_appointment_references"], 2)
        self.assertEqual(report["calendar"]["references_without_matching_event"], 1)
        self.assertTrue(report["readiness"]["requires_review"])

    def test_report_does_not_emit_names_ids_answers_notes_or_event_ids(self):
        serialized = json.dumps(audit_application_data(
            self.applications, self.team_data, self.suite_db, self.pulse_db
        ))
        for private_value in (
            "Private Applicant", "Other Private Applicant", "Private interview notes",
            "111111111111111111", "222222222222222222", "evt_private", "int_private",
            "private-status-value-should-not-leak",
        ):
            with self.subTest(value=private_value):
                self.assertNotIn(private_value, serialized)

    def test_missing_reference_databases_are_reported_as_unavailable(self):
        report = audit_application_data(self.applications, self.team_data)
        self.assertFalse(report["interviews"]["database_provided"])
        self.assertFalse(report["calendar"]["database_provided"])
        self.assertTrue(report["readiness"]["requires_review"])

    def test_sqlite_inputs_are_read_only_and_existing_metadata_is_unchanged(self):
        with sqlite3.connect(self.suite_db) as con:
            con.execute("PRAGMA user_version=17")
        audit_application_data(self.applications, self.team_data, self.suite_db, self.pulse_db)
        with sqlite3.connect(self.suite_db) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 17)

    def test_non_object_top_level_data_is_rejected(self):
        with self.assertRaises(ValueError):
            audit_application_data([], self.team_data)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            audit_application_data(self.applications, [])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
