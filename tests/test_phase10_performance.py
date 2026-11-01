"""Phase 10: performance changes must give identical results and bounded work per page.

* member_stats with a stats_context: the per-member SQLite reads (tasks, training attempts, profile) became one query
  each and the shift history is grouped once – the figures must equal the old per-member path exactly.
* /ultimate/achievements built a complete stats_context (all JSON files, 5000 tickets) for every team member.
* /team read applications.json once per member without a stored team_since.
Only temp files and databases, fake login – no Discord, no production data.
"""
import json
import random
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_next
import pulse_ultimate
import webserver as ws
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401
from tests.test_pulse_admin_area import ADMIN, GUILD, fake_auth_for

BROWSER = {"Origin": "http://testserver"}

LEGACY_KEYS = ("shifts", "claimed", "closed", "team_data", "config")


class _Temp(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
            patch.object(ws, "BASE_DIR", base), patch.object(ws, "DATA_DIR", base),
            patch.object(ws, "SHIFTS_FILE", str(base / "shifts.json")),
            patch.object(ws, "DATA_FILE", str(base / "team_data.json")),
            patch.object(ws, "APPS_FILE", str(base / "applications.json")),
            patch.object(ws, "log_audit", lambda *a, **k: None),
            patch.object(pulse_ultimate, "_INIT", False),
            patch.object(pulse_next, "_INIT", False),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        pulse_next.setup_next()
        team_role = SimpleNamespace(id=100, name="Team", position=2)
        self.people = [SimpleNamespace(id=500 + i, display_name=f"P{i}", name=f"p{i}", bot=False, roles=[team_role],
                                       status="online", joined_at=None) for i in range(8)]

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def seed(self):
        rng = random.Random(10)
        now = ws.now_de()
        history = []
        for i in range(600):
            day = now - timedelta(days=rng.randint(0, 40))
            history.append({"mod_id": str(rng.choice([500, 501, 502, 503, 999, "kaputt"])), "date": day.strftime("%Y-%m-%d"),
                            "duration_seconds": rng.choice([60, 3600, "120", None]),
                            "duration_confirmed": rng.choice([True, False, None])})
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {"501": {"date": now.strftime("%Y-%m-%d"),
                                                                "started_at_iso": now.isoformat(), "status": "online"}},
                                      "history": history})
        ws.save_json(ws.DATA_FILE, {"502": {"warns": [{"reason": "x", "active": True}]}})
        con = sqlite3.connect(pulse_db.DB_PATH)
        try:
            info = list(con.execute("PRAGMA table_info(tasks)"))
            cols = [r[1] for r in info]
            for i in range(2600):  # member 500 has more than the 1000-task cap of the old per-member query
                row = {r[1]: ("x" if r[3] else None) for r in info}
                row.update(id=f"t{i}", title="T", status=rng.choice(["open", "done", "archived", None]),
                           priority=rng.choice(["urgent", "high", "normal", "low"]),
                           assignee_id=str(500 if i < 1500 else rng.choice([501, 502, 504])),
                           due_at=None if i % 3 else f"2026-11-{1 + i % 28:02d}", created_at=f"2026-10-01T00:{i // 60 % 60:02d}:{i % 60:02d}.{i:06d}")
                con.execute(f"INSERT INTO tasks({','.join(cols)}) VALUES({','.join('?' * len(cols))})", [row[c] for c in cols])
            for i in range(700):  # member 501 has more than the 200-attempt window
                con.execute("INSERT INTO training_attempts VALUES (?,?,?,?,?,?,?)",
                            (f"a{i}", "tr", str(501 if i < 450 else rng.choice([500, 503])), "P", 80, rng.choice([0, 1]),
                             f"2026-10-{1 + i % 28:02d}T{i % 24:02d}:{i % 60:02d}:00.{i:06d}"))
            con.commit()
        finally:
            con.close()


class MemberStatsEquivalenceTests(_Temp):
    def test_prefetched_context_equals_old_per_member_path(self):
        self.seed()
        shared = pulse_ultimate.stats_context()
        legacy = {k: shared[k] for k in LEGACY_KEYS}
        for person in self.people:
            self.assertEqual(pulse_ultimate.member_stats(person, shared), pulse_ultimate.member_stats(person, legacy),
                             person.id)
        figures = pulse_ultimate.member_stats(self.people[0], shared)
        self.assertEqual(figures["tasks"], 1000)  # the per-member cap stays
        self.assertGreater(pulse_ultimate.member_stats(self.people[1], shared)["training_passed"], 0)

    def test_renamed_or_new_member_still_updates_the_profile(self):
        shared = pulse_ultimate.stats_context()
        person = self.people[0]
        self.assertEqual(pulse_ultimate.member_stats(person, shared)["profile"]["display_name"], "P0")
        person.display_name = "P0 neu"
        self.assertEqual(pulse_ultimate.member_stats(person, shared)["profile"]["display_name"], "P0 neu")
        self.assertEqual(pulse_ultimate.get_profile("500")["display_name"], "P0 neu")

    def test_broken_history_entry_falls_back_to_the_old_filter(self):
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {}, "history": ["kaputt"]})
        shared = pulse_ultimate.stats_context()
        self.assertIsNone(shared["history_by_member"])
        with self.assertRaises(AttributeError):  # exactly as before: the old filter calls .get on every entry
            pulse_ultimate.member_stats(self.people[0], shared)

    def test_connections_do_not_grow_with_the_team(self):
        self.seed()
        opened = []
        real = sqlite3.connect

        def counting(*a, **k):
            opened.append(1)
            return real(*a, **k)

        def connections_for(people):
            for person in people:  # profiles exist, as on every page view after the first
                pulse_ultimate.ensure_profile(person)
            opened.clear()
            with patch.object(sqlite3, "connect", counting):
                shared = pulse_ultimate.stats_context()
                for person in people:
                    pulse_ultimate.member_stats(person, shared)
            return len(opened)

        self.assertEqual(connections_for(self.people[:2]), connections_for(self.people))


class AllStatsTests(_Temp):
    def test_record_score_only_for_members_without_todays_snapshot(self):
        self.seed()
        guild = SimpleNamespace(members=self.people)
        with patch.object(ws, "load_config", lambda: {"team_role_ids": [100]}):
            first = pulse_next.all_stats(guild)
            calls = []
            real = pulse_next.record_score
            with patch.object(pulse_next, "record_score", lambda m, ctx=None: calls.append(m.id) or real(m, ctx)):
                second = pulse_next.all_stats(guild)
                self.assertEqual(calls, [])  # everyone has today's snapshot – record_score returned False before
                newcomer = SimpleNamespace(id=777, display_name="Neu", name="neu", bot=False, roles=self.people[0].roles,
                                           status="online", joined_at=None)
                pulse_next.all_stats(SimpleNamespace(members=self.people + [newcomer]))
                self.assertEqual(calls, [777])
        self.assertEqual([(m.id, s) for m, s in first], [(m.id, s) for m, s in second])
        con = sqlite3.connect(pulse_db.DB_PATH)
        try:
            snapshots = con.execute("SELECT COUNT(*), COUNT(DISTINCT user_id) FROM next_score_snapshots").fetchone()
        finally:
            con.close()
        self.assertEqual(snapshots, (9, 9))  # one per member and day, as before


class PageWorkTests(_Temp):
    def setUp(self):
        super().setUp()
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers=BROWSER)

    def test_achievements_builds_the_context_once_and_keeps_the_leaderboard(self):
        self.seed()
        calls = []
        real = pulse_ultimate.stats_context
        with patch.object(ws, "auth", fake_auth_for(ADMIN)), patch.object(GUILD, "members", self.people), \
                patch.object(ws, "load_config", lambda: {"team_role_ids": [100]}), \
                patch.object(pulse_ultimate, "stats_context", lambda: calls.append(1) or real()):
            response = self.client.get("/ultimate/achievements")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(calls), 1)
        self.assertIn("P7", response.text)
        con = sqlite3.connect(pulse_db.DB_PATH)
        try:
            unlocked = dict(con.execute("SELECT user_id, COUNT(*) FROM next_member_achievements GROUP BY user_id").fetchall())
        finally:
            con.close()
        expected = {}
        shared = real()
        for person in self.people:
            s = pulse_ultimate.member_stats(person, shared)
            n = sum((s["total_seconds"] >= 10 * 3600, s["total_seconds"] >= 50 * 3600, s["total_seconds"] >= 100 * 3600,
                     s["closed_tickets"] >= 10, s["closed_tickets"] >= 50, s["training_passed"] >= 5, s["score"] >= 90,
                     s["activity_pct"] >= 100))
            if n:
                expected[str(person.id)] = n
        self.assertEqual(unlocked, expected)
        self.assertIn(f"{expected.get('501', 0)} Achievements", response.text)

    def test_score_pages_render_from_the_worker_thread(self):
        self.seed()
        with patch.object(ws, "auth", fake_auth_for(ADMIN)), patch.object(GUILD, "members", self.people), \
                patch.object(ws, "load_config", lambda: {"team_role_ids": [100]}):
            for url in ("/ultimate/reports", "/ultimate/reports.csv", "/ultimate/scoreboard", "/ultimate/team-trends"):
                with self.subTest(url=url):
                    response = self.client.get(url)
                    self.assertEqual(response.status_code, 200, response.text[:300])
                    self.assertTrue(any(p.display_name in response.text for p in self.people))
            csv_rows = self.client.get("/ultimate/reports.csv").text.strip().splitlines()
        self.assertEqual(len(csv_rows), 1 + len(self.people))

    def test_team_list_reads_applications_once(self):
        now = ws.now_de()
        ws.save_json(ws.APPS_FILE, {"a1": {"user_id": "503", "status": "accepted",
                                           "decided_at": (now - timedelta(days=30)).isoformat()}})
        for person in self.people:
            person.display_avatar = SimpleNamespace(url="https://cdn.example/a.png")
            person.activities = ()
            person.roles[0].color = SimpleNamespace(value=0x5B5CF0)
            person.top_role, person.nick = person.roles[0], None
        reads = []
        real = ws.load_json
        base_auth = fake_auth_for(ADMIN)

        def team_auth(*a, **k):
            ctx = base_auth(*a, **k)
            ctx.config = {"team_role_ids": [100]}
            return ctx
        with patch.object(ws, "auth", team_auth), patch.object(GUILD, "members", self.people), \
                patch.object(ws, "load_json", lambda path, default: reads.append(Path(path).name) or real(path, default)):
            response = self.client.get("/team")
        self.assertEqual(response.status_code, 200, response.text[:500])
        self.assertEqual(reads.count("applications.json"), 1)
        self.assertIn((now - timedelta(days=30)).strftime("%d.%m.%Y"), response.text)

    def test_team_since_for_without_apps_reads_the_file_as_before(self):
        ws.save_json(ws.APPS_FILE, {"a1": {"user_id": "500", "status": "hired", "decided_at": "2026-01-05T10:00:00+01:00"}})
        person = self.people[0]
        self.assertEqual(ws.team_since_for(person), "05.01.2026")
        self.assertEqual(ws.team_since_for(person, None, lambda: json.loads("{}")), "unbekannt")
        self.assertEqual(ws.team_since_for(person, {"team_since": "2025-03-01T08:00:00"}), "01.03.2025")


if __name__ == "__main__":
    unittest.main()
