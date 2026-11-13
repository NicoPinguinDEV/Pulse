"""Audit #83/#85: shift validation, overlap protection and minimum-staffing gap detection."""
import tempfile
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_schedule_rules as rules
import webserver as ws
from cogs import pulse_suite as suite

GUILD = "155555555555555555"


def shift(day, start, end, who, sid="x"):
    return {"id": sid, "date": day, "start_time": start, "end_time": end, "assignee_id": who}


class RuleTests(unittest.TestCase):
    def test_validate_shift_rejects_bad_input_and_normalizes(self):
        ok = rules.validate_shift("2026-11-20", "  Frühdienst ", "08:00", "12:00")
        self.assertEqual((ok["shift_name"], ok["start_time"], ok["end_time"]), ("Frühdienst", "08:00", "12:00"))
        for args in (("2026-13-40", "A", "08:00", "12:00"), ("2026-11-20", "", "08:00", "12:00"),
                     ("2026-11-20", "A", "25:00", "12:00"), ("2026-11-20", "A", "08:00", "08:00"),
                     ("2026-11-20", "A", "06:00", "23:00"), ("morgen", "A", "08:00", "12:00")):
            with self.assertRaises(ValueError, msg=args):
                rules.validate_shift(*args)

    def test_overnight_shift_is_allowed_and_conflicts_cross_midnight(self):
        night = rules.validate_shift("2026-11-20", "Nacht", "22:00", "04:00")
        self.assertEqual(night["end_time"], "04:00")
        new = dict(night, assignee_id="1")
        clash = rules.find_conflicts(new, [shift("2026-11-21", "03:00", "05:00", "1")])
        self.assertEqual(len(clash), 1)

    def test_conflicts_only_for_same_person_and_touching_edges_are_fine(self):
        new = shift("2026-11-20", "10:00", "12:00", "1", "new")
        existing = [shift("2026-11-20", "12:00", "14:00", "1"), shift("2026-11-20", "08:00", "10:00", "1"),
                    shift("2026-11-20", "10:00", "12:00", "2")]
        self.assertEqual(rules.find_conflicts(new, existing), [])
        self.assertEqual(len(rules.find_conflicts(new, existing + [shift("2026-11-20", "11:00", "13:00", "1")])), 1)

    def test_minimum_validation(self):
        self.assertEqual(rules.validate_minimum("", "08:00", "12:00", "2")["weekday"], None)
        self.assertEqual(rules.validate_minimum("6", "08:00", "12:00", 2)["weekday"], 6)
        for args in (("7", "08:00", "12:00", 1), ("x", "08:00", "12:00", 1), ("1", "12:00", "08:00", 1),
                     ("1", "08:00", "12:00", 0), ("1", "08:00", "12:00", 51), ("1", "08:00", "12:00", "abc")):
            with self.assertRaises(ValueError, msg=args):
                rules.validate_minimum(*args)

    def test_gaps_are_merged_windows_and_count_distinct_people(self):
        monday = date(2026, 11, 16)
        minimums = [{"id": "r", "weekday": 0, "start_time": "08:00", "end_time": "12:00", "min_staff": 2}]
        planned = [shift("2026-11-16", "08:00", "12:00", "1"), shift("2026-11-16", "08:00", "10:00", "1", "dup"),
                   shift("2026-11-16", "09:00", "11:00", "2")]
        gaps = rules.coverage_gaps(planned, minimums, monday, 7)
        self.assertEqual([(g["start_time"], g["end_time"], g["planned_min"]) for g in gaps],
                         [("08:00", "09:00", 1), ("11:00", "12:00", 1)])
        self.assertEqual(rules.coverage_gaps(planned, minimums, monday + timedelta(days=1), 6), [])

    def test_no_shifts_gives_full_window_gap_and_no_rules_gives_none(self):
        minimums = [{"weekday": None, "start_time": "10:00", "end_time": "11:00", "min_staff": 1}]
        gaps = rules.coverage_gaps([], minimums, date(2026, 11, 16), 2)
        self.assertEqual([(g["date"], g["planned_min"]) for g in gaps], [("2026-11-16", 0), ("2026-11-17", 0)])
        self.assertEqual(rules.coverage_gaps([], [], date(2026, 11, 16), 7), [])

    def test_overnight_shift_covers_early_morning_of_next_day(self):
        minimums = [{"weekday": None, "start_time": "01:00", "end_time": "02:00", "min_staff": 1}]
        gaps = rules.coverage_gaps([shift("2026-11-16", "22:00", "04:00", "1")], minimums, date(2026, 11, 17), 1)
        self.assertEqual(gaps, [])


class ScheduleRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
                        patch.object(suite, "DB_PATH", str(Path(self.tmp.name) / "suite.db"))]
        for item in self.patches:
            item.start()
        pulse_db.init_db()
        suite.register_routes()
        suite.init_db()
        member = SimpleNamespace(id=101, display_name="Alice", roles=[], bot=False, status="online")
        guild = SimpleNamespace(id=int(GUILD), members=[member], roles=[], get_role=lambda _: None,
                                get_member=lambda i: member if int(i) == 101 else None)
        self.ctx = SimpleNamespace(user={"id": "900", "global_name": "Leitung"}, guild=guild, guild_name="G", member=None,
                                   perms={"is_admin": True, "can_view_dashboard": True}, config={}, msg="", msg_ok=True)
        self.client = TestClient(ws.app, raise_server_exceptions=False)
        self.day = (datetime.now().date() + timedelta(days=3)).isoformat()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def post(self, path, data):
        with patch.object(ws, "auth", lambda *a, **k: self.ctx):
            return self.client.post(path, data=data, follow_redirects=False)

    def count(self, table):
        with suite.cx() as c:
            return c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def create(self, start="09:00", end="12:00", day=None):
        return self.post("/suite/schedule/create", {"date": day or self.day, "shift_name": "Früh", "start_time": start,
                                                    "end_time": end, "assignee_id": "101"})

    def test_create_rejects_invalid_and_overlapping_without_writing(self):
        self.assertEqual(self.create("12:00", "12:00").status_code, 400)
        self.assertEqual(self.create(day="kaputt").status_code, 400)
        self.assertEqual(self.count("suite_schedule"), 0)
        self.assertEqual(self.create().status_code, 303)
        self.assertEqual(self.create("11:00", "13:00").status_code, 409)
        self.assertEqual(self.count("suite_schedule"), 1)
        self.assertEqual(self.create("12:00", "14:00").status_code, 303)

    def test_delete_removes_shift_notifies_and_blocks_open_swaps(self):
        self.create()
        with suite.cx() as c:
            shift_id = c.execute("SELECT id FROM suite_schedule").fetchone()[0]
        self.assertEqual(self.post("/suite/schedule/missing/delete", {}).status_code, 404)
        self.assertEqual(self.count("suite_schedule"), 1)
        with suite.cx() as c:
            c.execute("INSERT INTO suite_schedule_swap_requests(id,guild_id,request_shift_id,target_shift_id,requester_id,"
                      "requester_name,target_id,target_name,status,created_at,updated_at) VALUES('s',?,?,?,?,?,?,?,?,?,?)",
                      (GUILD, shift_id, "other", "1", "A", "2", "B", "pending_peer", "t", "t"))
        self.assertEqual(self.post(f"/suite/schedule/{shift_id}/delete", {}).status_code, 409)
        self.assertEqual(self.count("suite_schedule"), 1)
        with suite.cx() as c:
            c.execute("DELETE FROM suite_schedule_swap_requests")
        self.assertEqual(self.post(f"/suite/schedule/{shift_id}/delete", {}).status_code, 303)
        self.assertEqual(self.count("suite_schedule"), 0)

    def test_minimums_validate_are_guild_scoped_and_drive_the_gap_card(self):
        self.assertEqual(self.post("/suite/schedule/minimums/add", {"weekday": "9", "start_time": "08:00",
                                                                     "end_time": "12:00", "min_staff": "1"}).status_code, 400)
        self.assertEqual(self.count("suite_schedule_minimums"), 0)
        self.assertEqual(self.post("/suite/schedule/minimums/add", {"weekday": "", "start_time": "08:00",
                                                                     "end_time": "12:00", "min_staff": "2"}).status_code, 303)
        with suite.cx() as c:
            rule_id = c.execute("SELECT id FROM suite_schedule_minimums").fetchone()[0]
        with patch.object(ws, "auth", lambda *a, **k: self.ctx):
            page = self.client.get("/suite/schedule")
        self.assertEqual(page.status_code, 200)
        self.assertIn("0 von 2 geplant", page.text)
        other = SimpleNamespace(**{**vars(self.ctx), "guild": SimpleNamespace(**{**vars(self.ctx.guild), "id": 999})})
        with patch.object(ws, "auth", lambda *a, **k: other):
            self.assertEqual(self.client.post(f"/suite/schedule/minimums/{rule_id}/delete", follow_redirects=False).status_code, 404)
        self.assertEqual(self.count("suite_schedule_minimums"), 1)
        self.assertEqual(self.post(f"/suite/schedule/minimums/{rule_id}/delete", {}).status_code, 303)
        self.assertEqual(self.count("suite_schedule_minimums"), 0)


if __name__ == "__main__":
    unittest.main()
