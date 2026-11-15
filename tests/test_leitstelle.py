"""Audit #173/#174/#177: control room aggregation, rendering and the leadership-only page (synthetic data, no Discord)."""
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_leitstelle as ls
import webserver as ws
from tests.test_teamakte_pages import LEAD, TEAM, Base

UTC = timezone.utc
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)  # Monday 12:00 UTC


def ticket(hours_ago, status="open", priority="normal", claimed="", name="Spieler", **extra):
    return {"id": f"t{hours_ago}{priority}{claimed}", "opened_at": (NOW - timedelta(hours=hours_ago)).isoformat(), "status": status,
            "priority": priority, "claimed_by_id": claimed, "claimed_by_name": "", "user_name": name, "category": "Support", **extra}


def shift(uid, days_ago, hours=2, hour=11):
    start = (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=0)
    return {"mod_id": uid, "mod_name": f"M{uid}", "started_at_iso": start.isoformat(), "duration_seconds": int(hours * 3600)}


DATA = {
    "members": [("1", "Ann"), ("2", "Bo"), ("3", "Cy")],
    "active_shifts": {"1": {"status": "online", "started_at_iso": (NOW - timedelta(minutes=90)).isoformat()}, "2": {"status": "break", "started_at_iso": (NOW - timedelta(minutes=10)).isoformat()},
                      "3": {"status": "ended"}},
    "history": [shift("1", 7), shift("2", 7), shift("3", 14), shift("1", 14), shift("2", 14)],
    "tickets": [ticket(30), ticket(1, priority="urgent"), ticket(2, claimed="1"), ticket(5, "closed")],
    "tasks": [{"status": "open", "assignee_id": "1", "due_at": (NOW - timedelta(days=1)).isoformat(), "priority": "high"},
              {"status": "open", "assignee_id": "", "priority": "normal"}, {"status": "done", "assignee_id": "1"}],
    "cases": [{"id": "c1", "case_no": "PX-2026-1", "title": "Dringend", "priority": "urgent", "status": "open"},
              {"id": "c2", "case_no": "PX-2026-2", "title": "Ruhig", "priority": "normal", "status": "open"},
              {"id": "c3", "case_no": "PX-2026-3", "title": "Zu", "priority": "urgent", "status": "closed"}],
    "reports": [{"status": "open", "created_at": (NOW - timedelta(hours=60)).isoformat()}, {"status": "open", "created_at": (NOW - timedelta(hours=2)).isoformat()},
                {"status": "closed", "created_at": (NOW - timedelta(hours=90)).isoformat()}],
    "events": [{"title": "Teamsitzung", "starts_at": (NOW + timedelta(days=1)).isoformat(), "category": "meeting"},
               {"title": "Zu spät", "starts_at": (NOW + timedelta(days=9)).isoformat()}, {"title": "Vorbei", "starts_at": (NOW - timedelta(days=1)).isoformat()}],
    "departments": [{"id": "d1", "name": "Support", "members": ["1", "3"]}, {"id": "d2", "name": "Moderation", "members": []}],
    "voice_waiting": 2,
}


class BuildCase(unittest.TestCase):
    def setUp(self):
        self.snap = ls.build(DATA, NOW)

    def test_on_duty_and_coverage(self):
        self.assertEqual([(d["name"], d["status"], d["minutes"]) for d in self.snap["on_duty"]], [("Ann", "online", 90), ("Bo", "break", 10)])
        cov = self.snap["coverage"]
        self.assertEqual(cov["now"], 2)
        self.assertGreater(cov["typical"], 0)
        self.assertIn(cov["state"], ("im üblichen Rahmen", "über dem Üblichen", "unter dem Üblichen"))

    def test_coverage_states(self):
        low = ls._coverage([shift(str(i), d, 2) for d in (7, 14, 21) for i in range(4)], 0, NOW)
        self.assertEqual(low["state"], "unter dem Üblichen")
        self.assertEqual(ls._coverage([], 3, NOW)["state"], "keine Vergleichsdaten")

    def test_support_queue_counts_and_order(self):
        sup = self.snap["support"]
        self.assertEqual((sup["open"], sup["unclaimed"], sup["voice_waiting"]), (3, 2, 2))
        self.assertEqual(sup["overdue"], 2)  # 30 h normal and 1 h urgent
        self.assertEqual([q["minutes"] for q in sup["queue"]], [1800, 60])  # oldest first
        self.assertTrue(all(q["overdue"] for q in sup["queue"]))

    def test_tasks_and_escalations(self):
        self.assertEqual(self.snap["tasks"], {"open": 2, "overdue": 1, "unassigned": 1})
        esc = self.snap["escalations"]
        self.assertEqual([c["no"] for c in esc["urgent_cases"]], ["PX-2026-1"])
        self.assertEqual((esc["open_cases"], esc["stale_reports"]), (2, 1))
        self.assertTrue(all(f["severity"] in ("hoch", "mittel") for f in esc["findings"]))

    def test_upcoming_only_within_seven_days(self):
        self.assertEqual([e["title"] for e in self.snap["upcoming"]], ["Teamsitzung"])

    def test_departments(self):
        deps = {d["name"]: d for d in self.snap["departments"]}
        self.assertEqual((deps["Support"]["members"], deps["Support"]["on_duty"], deps["Support"]["open_tasks"], deps["Support"]["open_tickets"]), (2, 1, 1, 1))
        self.assertEqual(deps["Support"]["hours_30d"], 6.0)
        self.assertEqual((deps["Moderation"]["members"], deps["Moderation"]["on_duty"]), (0, 0))

    def test_empty_and_garbage_input_never_raise(self):
        for bad in (None, {}, {"tickets": [None, "x"], "cases": [None], "reports": ["x"], "events": [None, {}], "departments": [{}], "active_shifts": {"1": None}}):
            snap = ls.build(bad, NOW)
            self.assertEqual(snap["support"]["open"], 0)
            self.assertEqual(snap["on_duty"], [])


class RenderAndRouteCase(Base):
    def setUp(self):
        super().setUp()
        import pulse_player_reports
        # Die Leitstelle liest Spielermeldungen; die Suite-DB darf nicht im Checkout entstehen.
        patcher = patch.object(pulse_player_reports, "DB_PATH", str(Path(self.tmp.name) / "pulse_suite.db"))
        patcher.start()
        self.patches.append(patcher)

    def test_render_escapes_everything(self):
        evil = dict(DATA, members=[("1", "<script>alert(1)</script>")], cases=[{"id": "x", "case_no": "<b>", "title": "<i>t</i>", "priority": "urgent", "status": "open"}])
        html = ls.render(ls.build(evil, NOW), ws.esc)
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertNotIn("<i>t</i>", html)
        self.assertIn("Support-Warteschlange", html)

    def test_page_is_leadership_only_and_renders(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with self.as_user(LEAD):
            resp = client.get("/leitstelle")
            self.assertEqual(resp.status_code, 200)
            self.assertIn("Leitstelle", resp.text)
        with self.as_user(TEAM):
            self.assertEqual(client.get("/leitstelle").status_code, 403)

    def test_a_failing_source_does_not_break_the_page(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with self.as_user(LEAD), patch("pulse_leitstelle._cases", side_effect=RuntimeError("db")), patch("pulse_leitstelle._reports", side_effect=RuntimeError("db")):
            self.assertEqual(client.get("/leitstelle").status_code, 200)


if __name__ == "__main__":
    unittest.main()
