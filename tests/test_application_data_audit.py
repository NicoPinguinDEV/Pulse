"""Tests for the read-only, privacy-safe Phase 15.6 application audit."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from application_data_audit import _calendar_events, audit_application_data  # noqa: E402


class ApplicationDataAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.suite_db = self.base / "pulse_suite.db"
        self.pulse_db = self.base / "pulse.db"

        with closing(sqlite3.connect(self.suite_db)) as con:
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

        with closing(sqlite3.connect(self.pulse_db)) as con:
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
        self.assertEqual(report["interviews"]["orphan_interview_records"], 1)
        self.assertEqual(report["interviews"]["application_interview_references"], 3)
        self.assertEqual(report["interviews"]["application_refs_without_matching_interview"], 1)
        self.assertEqual(report["interviews"]["application_interview_id_mismatches"], 1)
        self.assertEqual(report["calendar"]["application_appointment_references"], 2)
        self.assertEqual(report["calendar"]["references_without_matching_event"], 1)
        self.assertEqual(report["calendar"]["event_ids_shared_by_multiple_applications"], 0)
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

    def test_database_with_missing_expected_tables_requires_review_even_without_refs(self):
        empty_suite = self.base / "empty_suite.db"
        empty_pulse = self.base / "empty_pulse.db"
        sqlite3.connect(empty_suite).close()
        sqlite3.connect(empty_pulse).close()

        report = audit_application_data({}, {}, empty_suite, empty_pulse)

        self.assertFalse(report["interviews"]["table_found"])
        self.assertFalse(report["calendar"]["table_found"])
        self.assertTrue(report["readiness"]["requires_review"])

    def test_tables_with_incomplete_schema_require_review(self):
        bad_suite = self.base / "bad_suite.db"
        bad_pulse = self.base / "bad_pulse.db"
        with closing(sqlite3.connect(bad_suite)) as con:
            con.execute("CREATE TABLE suite_interviews(id TEXT)")
        with closing(sqlite3.connect(bad_pulse)) as con:
            con.execute("CREATE TABLE calendar_events(title TEXT)")

        report = audit_application_data({}, {}, bad_suite, bad_pulse)

        self.assertTrue(report["interviews"]["table_found"])
        self.assertFalse(report["interviews"]["schema_valid"])
        self.assertTrue(report["calendar"]["table_found"])
        self.assertFalse(report["calendar"]["schema_valid"])
        self.assertTrue(report["readiness"]["requires_review"])

    def test_reference_ids_must_be_primary_keys_and_duplicate_calendar_ids_are_detected(self):
        bad_suite = self.base / "suite_without_primary_key.db"
        bad_pulse = self.base / "calendar_without_primary_key.db"
        with closing(sqlite3.connect(bad_suite)) as con:
            con.execute(
                "CREATE TABLE suite_interviews(id TEXT, application_id TEXT, marker TEXT, PRIMARY KEY(id, marker))"
            )
            con.execute("INSERT INTO suite_interviews VALUES(?,?,?)", ("same_interview", "app_a", "one"))
            con.execute("INSERT INTO suite_interviews VALUES(?,?,?)", ("same_interview", "app_b", "two"))
        with closing(sqlite3.connect(bad_pulse)) as con:
            con.execute("CREATE TABLE calendar_events(id TEXT, marker TEXT, PRIMARY KEY(id, marker))")
            con.execute("INSERT INTO calendar_events VALUES(?,?)", ("same_event", "one"))
            con.execute("INSERT INTO calendar_events VALUES(?,?)", ("same_event", "two"))

        report = audit_application_data({}, {}, bad_suite, bad_pulse)

        self.assertFalse(report["interviews"]["schema_valid"])
        self.assertEqual(report["interviews"]["duplicate_interview_ids"], 1)
        self.assertFalse(report["calendar"]["schema_valid"])
        self.assertEqual(report["calendar"]["duplicate_event_ids"], 1)
        self.assertTrue(report["readiness"]["requires_review"])


    def test_detects_interview_rows_without_application_or_interview_ids(self):
        with closing(sqlite3.connect(self.suite_db)) as con:
            con.execute(
                "INSERT INTO suite_interviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("int_unassigned", None, "", "", "{}", None, "", "", "", "", ""),
            )
            con.execute(
                "INSERT INTO suite_interviews VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                ("", "hired_app", "", "", "{}", None, "", "", "", "", ""),
            )

        report = audit_application_data(
            self.applications, self.team_data, self.suite_db, self.pulse_db
        )

        self.assertEqual(report["interviews"]["interview_rows_without_application_id"], 1)
        self.assertEqual(report["interviews"]["interview_rows_without_id"], 1)
        self.assertEqual(report["interviews"]["orphan_interview_records"], 2)
        self.assertTrue(report["readiness"]["requires_review"])

    def test_detects_calendar_event_referenced_by_multiple_applications(self):
        applications = dict(self.applications)
        applications["another_application"] = {
            "status": "pending",
            "interview_event_id": "evt_private",
        }

        report = audit_application_data(
            applications, self.team_data, self.suite_db, self.pulse_db
        )

        self.assertEqual(report["calendar"]["event_ids_shared_by_multiple_applications"], 1)
        self.assertTrue(report["readiness"]["requires_review"])

    def test_calendar_sqlite_connection_is_closed(self):
        connection = MagicMock()
        table_cursor = MagicMock()
        table_cursor.fetchone.return_value = (1,)
        schema_cursor = MagicMock()
        schema_cursor.fetchall.return_value = [{"name": "id"}]
        rows_cursor = MagicMock()
        rows_cursor.fetchall.return_value = [{"id": "event"}]
        connection.execute.side_effect = [table_cursor, schema_cursor, rows_cursor]

        with patch("application_data_audit._readonly_db", return_value=connection):
            result = _calendar_events(self.pulse_db)

        self.assertTrue(result["table_found"])
        connection.close.assert_called_once_with()

    def test_non_string_status_values_are_reported_without_crashing(self):
        applications = {
            "bad_list_status": {"user_id": "111", "status": ["hired"]},
            "bad_object_status": {"user_id": "222", "status": {"value": "accepted"}},
        }

        report = audit_application_data(applications, {}, self.suite_db, self.pulse_db)

        self.assertEqual(report["applications"]["unknown_or_missing_states"], 2)
        self.assertEqual(report["employment"]["accepted_or_hired_records"], 0)
        self.assertTrue(report["readiness"]["requires_review"])


    def test_noncanonical_employment_status_is_not_accepted_by_runtime_or_audit(self):
        applications = {
            "hired_app": {
                "user_id": "111111111111111111",
                "status": " HIRED ",
            }
        }
        report = audit_application_data(applications, self.team_data, self.suite_db, self.pulse_db)

        self.assertNotIn("hired", report["applications"]["states"])
        self.assertEqual(report["applications"]["unknown_or_missing_states"], 1)
        self.assertEqual(report["employment"]["accepted_or_hired_records"], 0)
        self.assertTrue(report["readiness"]["requires_review"])

    def test_employment_decided_at_fallback_matches_team_since_runtime(self):
        applications = {
            "hired_app": {
                "user_id": "111111111111111111",
                "status": "hired",
                "decided_at": "02.11.2026 10:30",
            }
        }
        report = audit_application_data(applications, {}, self.suite_db, self.pulse_db)

        self.assertEqual(report["employment"]["accepted_or_hired_records"], 1)
        self.assertEqual(report["employment"]["records_without_team_since"], 0)

    def test_whitespace_padded_user_id_does_not_match_runtime_member_id(self):
        applications = {
            "hired_app": {
                "user_id": " 111111111111111111 ",
                "status": "hired",
                "decided_at": "02.11.2026 10:30",
            }
        }
        report = audit_application_data(
            applications,
            {"111111111111111111": {"team_since": "2026-10-01T10:00:00+02:00"}},
            self.suite_db,
            self.pulse_db,
        )

        self.assertEqual(report["applications"]["missing_user_ids"], 1)
        self.assertEqual(report["employment"]["records_without_team_since"], 1)
        self.assertTrue(report["readiness"]["requires_review"])


    def test_discord_id_does_not_replace_user_id_for_team_since_lookup(self):
        applications = {
            "hired_app": {
                "discord_id": "111111111111111111",
                "status": "hired",
                "decided_at": "invalid-date",
            }
        }
        report = audit_application_data(
            applications,
            {"111111111111111111": {"team_since": "2026-10-01T10:00:00+02:00"}},
            self.suite_db,
            self.pulse_db,
        )

        self.assertEqual(report["applications"]["missing_user_ids"], 1)
        self.assertEqual(report["employment"]["records_without_team_since"], 1)
        self.assertTrue(report["readiness"]["requires_review"])

    def test_missing_databases_require_review_even_without_application_references(self):
        report = audit_application_data({}, {})

        self.assertFalse(report["interviews"]["database_provided"])
        self.assertFalse(report["calendar"]["database_provided"])
        self.assertTrue(report["readiness"]["requires_review"])

    def test_sqlite_inputs_are_read_only_and_existing_metadata_is_unchanged(self):
        with closing(sqlite3.connect(self.suite_db)) as con:
            con.execute("PRAGMA user_version=17")
        audit_application_data(self.applications, self.team_data, self.suite_db, self.pulse_db)
        with closing(sqlite3.connect(self.suite_db)) as con:
            self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 17)

    def test_non_object_top_level_data_is_rejected(self):
        with self.assertRaises(ValueError):
            audit_application_data([], self.team_data)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            audit_application_data(self.applications, [])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
