"""Batch equivalence tests for the leadership team's per-member score calculations."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import cogs.pulse_suite as suite
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class SuiteTeamBatchMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        pulse_db.init_db()
        self.member_a = "100000000000000001"
        self.member_b = "100000000000000002"
        self._seed_tasks()
        self._seed_attempts()

    def _seed_tasks(self):
        with pulse_db.connect() as db:
            rows = []
            # This member alone has more than the page's global limit. Its per-member
            # first 1,000 rows must not evict another member's own tasks from the score.
            for i in range(1050):
                rows.append((
                    f"a-{i}", f"Aufgabe A {i}", "", self.member_a, "Mitglied A", "1", "Admin",
                    "done" if i % 4 == 0 else "open", "urgent", None,
                    f"2030-01-01T00:{i // 60:02d}:{i % 60:02d}", f"2030-01-01T00:{i // 60:02d}:{i % 60:02d}", None,
                ))
            for i in range(4):
                rows.append((
                    f"b-{i}", f"Aufgabe B {i}", "", self.member_b, "Mitglied B", "1", "Admin",
                    "done" if i < 2 else "open", "low", None,
                    f"2020-01-01T00:00:{i:02d}", f"2020-01-01T00:00:{i:02d}", None,
                ))
            db.executemany(
                "INSERT INTO tasks(id,title,description,assignee_id,assignee_name,creator_id,creator_name,"
                "status,priority,due_at,created_at,updated_at,archived_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                rows,
            )

    def _seed_attempts(self):
        with pulse_db.connect() as db:
            rows = []
            for uid, count in ((self.member_a, 215), (self.member_b, 7)):
                for i in range(count):
                    rows.append((
                        f"{uid}-{i}", "training-1", uid, "Teammitglied", 80,
                        1 if i % 3 == 0 else 0, f"2026-11-{1 + i // 24:02d}T{i % 24:02d}:00:00",
                    ))
            db.executemany(
                "INSERT INTO training_attempts(id,training_id,user_id,user_name,score,passed,created_at) "
                "VALUES (?,?,?,?,?,?,?)",
                rows,
            )

    def test_batch_totals_preserve_the_per_member_1000_task_and_200_attempt_caps(self):
        batch_tasks = pulse_db.task_totals_by_assignee(1000)
        batch_attempts = pulse_db.passed_attempts_by_user(200)
        selected_tasks = pulse_db.task_totals_by_assignee(1000, [self.member_b])
        selected_attempts = pulse_db.passed_attempts_by_user(200, [self.member_b])
        self.assertEqual(selected_tasks, {self.member_b: batch_tasks[self.member_b]})
        self.assertEqual(selected_attempts, {self.member_b: batch_attempts[self.member_b]})

        for uid in (self.member_a, self.member_b):
            expected_tasks = pulse_db.list_tasks(assignee_id=uid, limit=1000, include_archived=True)
            expected_attempts = pulse_db.attempts(user_id=uid, limit=200)
            expected_done = sum(row.get("status") in {"done", "archived"} for row in expected_tasks)
            expected_passed = sum(bool(row.get("passed")) for row in expected_attempts)
            self.assertEqual(batch_tasks[uid], (len(expected_tasks), expected_done), uid)
            self.assertEqual(batch_attempts[uid], expected_passed, uid)

        global_snapshot = pulse_db.list_tasks(limit=1000, include_archived=True)
        self.assertFalse(any(row["assignee_id"] == self.member_b for row in global_snapshot))
        self.assertEqual(batch_tasks[self.member_b][0], 4)

    def test_member_directory_snapshot_returns_all_rows_in_one_read(self):
        suite_tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(suite_tmp.cleanup)
        with patch.object(suite, "DB_PATH", str(Path(suite_tmp.name) / "suite.db")):
            suite.init_db()
            suite.upsert_members_batch([
                ("100000000000000001", "2026-11-20T20:00:00+00:00", "2026-12-04T20:00:00+00:00"),
                ("100000000000000002", "2026-11-20T20:01:00+00:00", "2026-12-04T20:01:00+00:00"),
            ])
            rows = suite.member_rows()
        self.assertEqual(set(rows), {"100000000000000001", "100000000000000002"})
        self.assertEqual(rows["100000000000000002"]["probation_end"], "2026-12-04T20:01:00+00:00")

    def test_batched_score_matches_the_previous_per_member_path_without_n_plus_one_reads(self):
        member = SimpleNamespace(id=int(self.member_b))
        uid = str(member.id)
        # create_task fixtures above use the same exact string ID; ticket and score inputs
        # below are intentionally small and independent of Discord.
        ticket_rows = [
            {"claimed_by_id": uid, "status": "closed"},
            {"claimed_by_id": uid, "status": "closed"},
            {"claimed_by_id": uid, "status": "open"},
            {"claimed_by_id": self.member_a, "status": "closed"},
        ]
        task_rows = pulse_db.list_tasks(assignee_id=uid, limit=1000, include_archived=True)
        attempts = pulse_db.attempts(user_id=uid, limit=200)
        old_passed = sum(1 for row in attempts if row.get("passed"))
        task_totals = pulse_db.task_totals_by_assignee(1000)
        passed_totals = pulse_db.passed_attempts_by_user(200)
        ticket_totals = {}
        for row in ticket_rows:
            if row.get("status") == "closed" and row.get("claimed_by_id") is not None:
                target = str(row["claimed_by_id"])
                ticket_totals[target] = ticket_totals.get(target, 0) + 1

        web = SimpleNamespace()
        with patch.object(suite, "weekly", return_value=3600), patch.object(suite, "warns", return_value=[]), \
                patch.object(suite, "setting", return_value=3):
            old_result = suite.team_score(
                member, web, task_rows=task_rows, ticket_rows=ticket_rows, team_data={}
            )
            with patch.object(suite.db, "list_tasks") as list_tasks, \
                    patch.object(suite.db, "attempts") as list_attempts, \
                    patch.object(suite.db, "list_tickets") as list_tickets:
                batch_result = suite.team_score(
                    member, web, team_data={}, task_totals=task_totals, ticket_totals=ticket_totals,
                    passed_attempts=passed_totals, weekly_goal_seconds=3 * 3600,
                    shift_state={"active_shifts": {}}, weekly_totals={uid: 3600},
                )
        self.assertEqual(old_passed, passed_totals[uid])
        self.assertEqual(batch_result, old_result)
        list_tasks.assert_not_called()
        list_attempts.assert_not_called()
        list_tickets.assert_not_called()


if __name__ == "__main__":
    unittest.main()
