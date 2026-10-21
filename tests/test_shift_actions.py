"""BOT-03: shared dashboard/Discord shift-action behavior with fake persistence."""
import copy
import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

import webserver as ws
from pulse_shift_actions import apply_shift_action, get_shift_status


class ShiftActionTests(unittest.TestCase):
    def setUp(self):
        self.shifts = {"active_shifts": {}, "history": []}
        self.saved = []
        self.now = datetime(2026, 10, 21, 12, 0, tzinfo=ZoneInfo("Europe/Berlin"))

        def load_shifts():
            return copy.deepcopy(self.shifts)

        def save_json(path, value):
            self.saved.append((path, copy.deepcopy(value)))
            if path == ws.SHIFTS_FILE:
                self.shifts = copy.deepcopy(value)

        def load_json(_path, default=None):
            return copy.deepcopy(default if default is not None else {})

        self.patchers = [
            patch.object(ws, "load_shifts", side_effect=load_shifts),
            patch.object(ws, "save_json", side_effect=save_json),
            patch.object(ws, "load_json", side_effect=load_json),
            patch.object(ws, "now_de", return_value=self.now),
            patch.object(ws, "active_warns", return_value=[]),
            patch.object(ws, "user_entry", return_value={}),
            patch.object(ws, "get_loas", return_value={}),
            patch.object(ws, "fmt_duration", side_effect=lambda seconds: f"{seconds}s"),
            patch.object(ws, "fmt_date", side_effect=lambda _value: "28.10.2026"),
            patch.object(ws, "shift_elapsed", side_effect=lambda shift: int(shift.get("elapsed_for_test", 0))),
            patch.object(ws, "shift_end_open_items_text", return_value=" · Noch offene Punkte."),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in reversed(self.patchers):
            p.stop()

    def test_start_creates_expected_record(self):
        result = apply_shift_action(42, "start")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "online")
        self.assertIn("Schicht gestartet", result["message"])
        row = self.shifts["active_shifts"]["42"]
        self.assertEqual(row["status"], "online")
        self.assertEqual(row["started_at_iso"], self.now.isoformat())
        self.assertEqual(row["segment_start_iso"], self.now.isoformat())
        self.assertEqual(row["date"], "2026-10-21")
        self.assertEqual(row["accumulated_seconds"], 0)
        self.assertEqual(len(self.saved), 1)

    def test_existing_shift_and_five_active_warnings_block_start(self):
        self.shifts["active_shifts"]["42"] = {"status": "online"}
        result = apply_shift_action(42, "start")
        self.assertFalse(result["ok"])
        self.assertIn("bereits eine laufende Schicht", result["message"])
        self.assertEqual(self.saved, [])

        self.shifts["active_shifts"] = {}
        with patch.object(ws, "active_warns", return_value=[{}, {}, {}, {}, {}]):
            result = apply_shift_action(42, "start")
        self.assertFalse(result["ok"])
        self.assertIn("5 aktive Verwarnungen", result["message"])
        self.assertEqual(self.saved, [])

    def test_break_resume_and_end_share_the_same_state_logic(self):
        self.shifts["active_shifts"]["42"] = {
            "status": "online",
            "started_at_iso": "2026-10-21T10:00:00+02:00",
            "segment_start_iso": "2026-10-21T10:00:00+02:00",
            "date": "2026-10-21",
            "accumulated_seconds": 0,
            "elapsed_for_test": 3600,
        }

        paused = apply_shift_action(42, "pause")
        self.assertTrue(paused["ok"])
        self.assertEqual(paused["status"], "break")
        self.assertEqual(self.shifts["active_shifts"]["42"]["accumulated_seconds"], 3600)
        self.assertIsNone(self.shifts["active_shifts"]["42"]["segment_start_iso"])

        resumed = apply_shift_action(42, "weiter")
        self.assertTrue(resumed["ok"])
        self.assertEqual(resumed["status"], "online")
        self.assertEqual(self.shifts["active_shifts"]["42"]["segment_start_iso"], self.now.isoformat())

        self.shifts["active_shifts"]["42"]["elapsed_for_test"] = 7200
        ended = apply_shift_action(42, "ende")
        self.assertTrue(ended["ok"])
        self.assertEqual(ended["status"], "offline")
        self.assertNotIn("42", self.shifts["active_shifts"])
        self.assertEqual(self.shifts["history"][-1]["duration_seconds"], 7200)
        self.assertIn("Noch offene Punkte", ended["message"])
        self.assertEqual([entry[0] for entry in self.saved], [ws.SHIFTS_FILE] * 3)

    def test_active_leave_is_preserved_and_only_informs_user(self):
        with patch.object(ws, "get_loas", return_value={
            "42": {"active": True, "bis": "2026-10-28"},
        }):
            result = apply_shift_action(42, "start")
        self.assertTrue(result["ok"])
        self.assertIn("28.10.2026", result["message"])
        self.assertTrue(ws.get_loas()["42"]["active"])
        self.assertTrue(self.shifts["active_shifts"]["42"])

    def test_invalid_action_and_status_read_do_not_write(self):
        result = apply_shift_action(42, "server-kick")
        self.assertFalse(result["ok"])
        self.assertEqual(self.saved, [])
        self.assertEqual(get_shift_status(42), {
            "active": False, "status": "offline", "duration_seconds": 0,
        })
        self.assertEqual(self.saved, [])


if __name__ == "__main__":
    unittest.main()
