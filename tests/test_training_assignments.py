"""Phase 20: assigned training completion is reflected without revoking old passes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from cogs import pulse_suite


class TrainingAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "pulse_suite.db")
        self.db_patch = patch.object(pulse_suite, "DB_PATH", self.db_path)
        self.db_patch.start()
        pulse_suite.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def _insert(self, assignment_id, training_id, user_id, status):
        with pulse_suite.cx() as connection:
            connection.execute(
                "INSERT INTO suite_training_assignments VALUES(?,?,?,?,?,?,?,?)",
                (assignment_id, training_id, user_id, "Teammitglied", status, "1", "Leitung", "2026-11-10T12:00:00+00:00"),
            )

    def _statuses(self):
        with pulse_suite.cx() as connection:
            return {
                row["id"]: row["status"]
                for row in connection.execute("SELECT id,status FROM suite_training_assignments")
            }

    def test_passed_attempt_completes_only_matching_assigned_rows(self):
        self._insert("a1", "training-1", "42", "assigned")
        self._insert("a2", "training-1", "42", "assigned")
        self._insert("a3", "training-1", "43", "assigned")
        self._insert("a4", "training-2", "42", "assigned")
        self._insert("a5", "training-1", "42", "passed")

        changed = pulse_suite.mark_training_assignment_result("training-1", 42, True)

        self.assertEqual(changed, 2)
        self.assertEqual(
            self._statuses(),
            {"a1": "passed", "a2": "passed", "a3": "assigned", "a4": "assigned", "a5": "passed"},
        )

    def test_failed_attempt_does_not_change_assignment_status(self):
        self._insert("a1", "training-1", "42", "assigned")
        self.assertEqual(pulse_suite.mark_training_assignment_result("training-1", 42, False), 0)
        self.assertEqual(self._statuses(), {"a1": "assigned"})

    def test_successful_retake_does_not_downgrade_an_existing_pass(self):
        self._insert("a1", "training-1", "42", "passed")
        self.assertEqual(pulse_suite.mark_training_assignment_result("training-1", 42, True), 0)
        self.assertEqual(self._statuses(), {"a1": "passed"})


if __name__ == "__main__":
    unittest.main()
