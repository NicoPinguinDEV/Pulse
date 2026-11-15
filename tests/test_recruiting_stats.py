"""Audit #102: recruiting statistics (pure computation on synthetic records + the staff-only route)."""
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_recruiting_stats as rs
import webserver as ws
from tests.test_interview_calendar import make_auth

NOW = datetime(2026, 11, 15, 12, 0)


def app(status, created, invited="", decided="", reviewers=(), reviewed=()):
    return {"status": status, "created_at": created, "interview_invited_at": invited, "decided_at": decided,
            "assignments": [{"reviewer_id": r, "reviewer_name": f"P{r}"} for r in reviewers],
            "reviews": {r: {"ratings": {"a": 3}} for r in reviewed}}


APPS = {
    "1": app("hired", "01.11.2026 10:00", "03.11.2026 10:00", "05.11.2026 10:00", reviewers=("10", "11"), reviewed=("10",)),
    "2": app("rejected", "02.11.2026 12:00", "", "03.11.2026 12:00", reviewers=("10",), reviewed=("10",)),
    "3": app("rejected", "02.11.2026 12:00", "04.11.2026 12:00", "06.11.2026 12:00"),
    "4": app("pending", "14.11.2026 12:00"),
    "5": app("interview", "10.11.2026 12:00", "11.11.2026 12:00", reviewers=("11",)),
    "6": app("weird", "kaputt"),
}


class ComputeCase(unittest.TestCase):
    def test_funnel_and_conversion(self):
        d = rs.compute(APPS, NOW)
        self.assertEqual(d["total"], 6)
        self.assertEqual(d["status"], {"pending": 1, "interview": 1, "hired": 1, "rejected": 2})
        self.assertEqual(d["other"], 1)
        self.assertEqual(d["decided"], 3)
        self.assertEqual(d["conversion"]["hired_of_decided_pct"], 33.3)
        self.assertEqual(d["conversion"]["interview_pct"], round(100 * 3 / 6, 1))  # hired, interview, rejected-after-interview

    def test_durations_use_only_readable_values_and_state_n(self):
        d = rs.compute(APPS, NOW)["durations"]
        self.assertEqual(d["to_invite"]["n"], 3)
        self.assertEqual(d["to_invite"]["avg_h"], round((48 + 48 + 24) / 3, 1))
        self.assertEqual(d["to_decision"]["n"], 3)
        self.assertEqual(d["interview_to_decision"]["n"], 2)  # the rejection without interview has no start
        self.assertEqual(d["open_age"]["n"], 2)
        self.assertEqual(d["open_age"]["max_h"], 120.0)  # #5: created 10.11. 12:00, now 15.11. 12:00

    def test_reviewer_table(self):
        rows = {r["id"]: r for r in rs.compute(APPS, NOW)["reviewers"]}
        self.assertEqual((rows["10"]["assigned"], rows["10"]["reviewed"], rows["10"]["completion_pct"]), (2, 2, 100.0))
        self.assertEqual((rows["11"]["assigned"], rows["11"]["reviewed"], rows["11"]["completion_pct"]), (2, 0, 0.0))

    def test_period_filter_excludes_old_and_unreadable(self):
        d = rs.compute(APPS, NOW, days=7)
        self.assertEqual(d["total"], 2)  # only #4 and #5
        self.assertEqual(rs.compute(APPS, NOW, days=30)["total"], 5)

    def test_empty_and_garbage_input_do_not_raise(self):
        for bad in ({}, None, {"x": "not a dict"}, {"y": {}}):
            d = rs.compute(bad, NOW)
            self.assertIsNone(d["conversion"]["hired_of_decided_pct"])
            self.assertEqual(d["durations"]["to_invite"]["n"], 0)

    def test_end_before_start_is_ignored(self):
        d = rs.compute({"a": app("hired", "05.11.2026 10:00", "01.11.2026 10:00", "02.11.2026 10:00")}, NOW)
        self.assertEqual(d["durations"]["to_invite"]["n"], 0)

    def test_parse_formats(self):
        self.assertEqual(rs.parse("01.11.2026 10:00"), datetime(2026, 11, 1, 10, 0))
        self.assertEqual(rs.parse("2026-11-01T10:00:00+00:00"), datetime(2026, 11, 1, 10, 0))
        self.assertIsNone(rs.parse("morgen"))
        self.assertIsNone(rs.parse(None))


class RouteCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(ws, "APPS_FILE", str(root / "applications.json")),
                        patch.object(ws, "AUDIT_FILE", str(root / "a.json")),
                        patch.object(ws, "compute_perms", lambda g, u, c: ({}, None))]
        for p in self.patches:
            p.start()
        ws.save_json(ws.APPS_FILE, APPS)
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def get(self, user, query=""):
        with patch.object(ws, "auth", make_auth(user)):
            return self.client.get("/applications/statistik" + query)

    def test_staff_sees_statistics_and_period_filter_works(self):
        resp = self.get("2")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Recruiting-Statistik", resp.text)
        self.assertIn("n=3", resp.text)
        self.assertEqual(self.get("2", "?days=7").status_code, 200)
        self.assertEqual(self.get("2", "?days=abc").status_code, 200)

    def test_non_staff_is_denied(self):
        self.assertEqual(self.get("3").status_code, 403)


if __name__ == "__main__":
    unittest.main()


class StaleCase(unittest.TestCase):
    def test_stale_lists_only_overdue_open_applications_with_open_reviewers(self):
        apps = {"p": app("pending", "10.11.2026 12:00", reviewers=("10", "11"), reviewed=("10",)),
                "fresh": app("pending", "14.11.2026 12:00", reviewers=("10",)),
                "i": app("interview", "01.11.2026 12:00", "05.11.2026 12:00"),
                "i2": app("interview", "01.11.2026 12:00", "12.11.2026 12:00"),
                "done": app("hired", "01.10.2026 12:00"), "bad": app("pending", "kaputt")}
        rows = rs.stale_applications(apps, NOW)
        self.assertEqual([r["id"] for r in rows], ["i", "p"])
        self.assertEqual(rows[1]["reviewers"], ["11"])
        self.assertEqual(rows[0]["reviewers"], [])
        self.assertEqual(rs.stale_applications({"x": "no"}, NOW), [])


class ReminderCase(unittest.TestCase):
    def test_notifications_go_to_open_reviewers_once_per_day_and_never_raise(self):
        apps = {"p": app("pending", "10.11.2026 12:00", reviewers=("10", "11"), reviewed=("10",))}
        calls = []
        with patch.object(ws, "load_json", return_value=apps), patch.object(ws, "now_de", return_value=NOW), \
                patch.object(ws.pulse_db, "notify", side_effect=lambda *a, **k: calls.append(a)):
            self.assertEqual(ws.send_stale_application_reminders(), 1)
        self.assertEqual(calls[0][0], "11")
        self.assertEqual(calls[0][5], "app-stale:p:11")
        self.assertEqual(calls[0][6], 86400)
        with patch.object(ws, "load_json", return_value=apps), patch.object(ws, "now_de", return_value=NOW), \
                patch.object(ws.pulse_db, "notify", side_effect=RuntimeError("db")):
            self.assertEqual(ws.send_stale_application_reminders(), 0)
