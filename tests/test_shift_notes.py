"""Shift notes #90: members can annotate only their own currently active shift."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pulse_db
import pulse_home
import webserver as ws
from pulse_shift_utils import shift_event_id


class ShiftNotesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.app = FastAPI()
        pulse_home.register(self.app)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.user = SimpleNamespace(
            user={"id": "7", "global_name": "Tess", "username": "tess"},
            perms={"can_view_dashboard": True},
            config={},
            member=None,
        )
        self.started = "2026-11-10T18:30:00+01:00"
        self.own_shift_id = shift_event_id("7", self.started)
        self.active_shifts = {
            "active_shifts": {
                "7": {
                    "status": "online",
                    "started_at_iso": self.started,
                    "segment_start_iso": self.started,
                    "date": "2026-11-10",
                }
            },
            "history": [],
        }

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def post_note(self, *, active=None, shift_id=None, note="Server wurde geprüft."):
        with (
            patch.object(ws, "auth", return_value=self.user),
            patch.object(ws, "load_member_shifts", return_value=active or self.active_shifts),
            patch.object(ws, "log_audit") as audit,
        ):
            response = self.client.post(
                "/shift/note",
                data={"shift_event_id": shift_id or self.own_shift_id, "note": note},
                follow_redirects=False,
            )
        return response, audit

    def test_member_can_add_a_note_to_their_own_active_shift(self):
        response, audit = self.post_note()
        self.assertEqual(response.status_code, 303)
        rows = pulse_db.shift_notes_for_member("7")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["shift_event_id"], self.own_shift_id)
        self.assertEqual(rows[0]["user_name"], "Tess")
        self.assertEqual(rows[0]["note"], "Server wurde geprüft.")
        audit.assert_called_once()
        self.assertNotIn("Server wurde geprüft.", str(audit.call_args))

    def test_notes_are_rejected_when_the_shift_is_not_active(self):
        inactive = {"active_shifts": {}, "history": []}
        response, _audit = self.post_note(active=inactive)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(pulse_db.shift_notes_for_member("7"), [])

    def test_note_cannot_be_attached_to_another_or_old_shift(self):
        response, _audit = self.post_note(shift_id=shift_event_id("8", self.started))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(pulse_db.shift_notes_for_member("7"), [])

        changed = {
            "active_shifts": {
                "7": {"status": "online", "started_at_iso": "2026-11-10T19:00:00+01:00"}
            },
            "history": [],
        }
        response, _audit = self.post_note(active=changed, shift_id=self.own_shift_id)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(pulse_db.shift_notes_for_member("7"), [])

    def test_empty_and_oversized_notes_are_rejected(self):
        for note in ("   ", "x" * 1001):
            with self.subTest(length=len(note)):
                response, _audit = self.post_note(note=note)
                self.assertEqual(response.status_code, 303)
        self.assertEqual(pulse_db.shift_notes_for_member("7"), [])

    def test_notes_are_isolated_per_member_and_queryable_by_shift(self):
        pulse_db.save_shift_note(self.own_shift_id, "7", "Tess", "Erste Notiz")
        pulse_db.save_shift_note(shift_event_id("8", self.started), "8", "Olli", "Andere Schicht")
        self.assertEqual([row["note"] for row in pulse_db.shift_notes_for_member("7")], ["Erste Notiz"])
        self.assertEqual([row["note"] for row in pulse_db.shift_notes_for_event(self.own_shift_id)], ["Erste Notiz"])

    def test_shift_notes_are_in_the_dashboard_widget_defaults(self):
        from pulse_home import DEFAULT_HOME_WIDGETS, normalize_home_widgets

        self.assertIn("shift_notes", DEFAULT_HOME_WIDGETS)
        self.assertIn("shift_notes", normalize_home_widgets(["shift_notes"]))

    def test_json_backup_restore_includes_shift_notes(self):
        pulse_db.save_shift_note(self.own_shift_id, "7", "Tess", "Übergabe an Spätdienst.")
        snapshot = pulse_db.export_state()
        self.assertEqual(len(snapshot["shift_notes"]), 1)
        with pulse_db.connect() as connection:
            connection.execute("DELETE FROM shift_notes")
        pulse_db.import_state(snapshot)
        self.assertEqual(
            pulse_db.shift_notes_for_member("7")[0]["note"],
            "Übergabe an Spätdienst.",
        )


if __name__ == "__main__":
    unittest.main()
