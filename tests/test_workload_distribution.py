"""Automatic workload distribution: fairness, ownership preservation and manager-only route."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pulse_db
import pulse_features
import webserver as ws


USER_A = "100000000000000001"
USER_B = "100000000000000002"
TEAM_ROLE = "1531303733311307997"


class WorkloadDistributionStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_unassigned_tasks_are_balanced_by_open_tasks_and_claimed_tickets(self):
        # A starts with two normal tasks; B has one high-priority claimed ticket.
        pulse_db.create_task("A-1", "", USER_A, "Alice", "1", "Leitung")
        pulse_db.create_task("A-2", "", USER_A, "Alice", "1", "Leitung")
        ticket_id = pulse_db.create_ticket("chan-1", "guild-1", "customer", "Kunde", "support", "high")
        self.assertTrue(pulse_db.claim_ticket(ticket_id, USER_B, "Bob"))

        urgent = pulse_db.create_task("Urgent unassigned", "", None, "", "1", "Leitung", "urgent")
        normal = pulse_db.create_task("Normal unassigned", "", None, "", "1", "Leitung", "normal")
        leftover = pulse_db.create_task("Still waiting", "", None, "", "1", "Leitung", "low")
        completed = pulse_db.create_task("Already done", "", None, "", "1", "Leitung")
        self.assertTrue(pulse_db.update_task(completed, status="done"))

        assignments = pulse_db.auto_distribute_unassigned_tasks(
            [{"id": USER_A, "name": "Alice"}, {"id": USER_B, "name": "Bob"}],
            "900", "Leitung", limit=2,
        )
        self.assertEqual([row["task_id"] for row in assignments], [urgent, normal])
        self.assertEqual([row["assignee_id"] for row in assignments], [USER_B, USER_A])
        self.assertEqual(pulse_db.get_task(urgent)["assignee_name"], "Bob")
        self.assertEqual(pulse_db.get_task(normal)["assignee_name"], "Alice")
        self.assertIsNone(pulse_db.get_task(leftover)["assignee_id"])
        self.assertIsNone(pulse_db.get_task(completed)["assignee_id"])
        self.assertEqual(pulse_db.get_task(completed)["status"], "done")
        self.assertEqual(pulse_db.get_task(urgent)["status"], "open")
        history = pulse_db.task_history(urgent)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["actor_id"], "900")
        self.assertIn("Arbeitslastverteilung", history[0]["note"])

    def test_existing_assignments_are_never_overwritten_and_invalid_candidates_are_ignored(self):
        existing = pulse_db.create_task("Already owned", "", USER_A, "Alice", "1", "Leitung")
        unassigned = pulse_db.create_task("Unassigned", "", None, "", "1", "Leitung")
        assignments = pulse_db.auto_distribute_unassigned_tasks(
            [{"id": USER_A, "name": "Alice"}, {"id": "not-an-id", "name": "Invalid"}],
            "900", "Leitung",
        )
        self.assertEqual(len(assignments), 1)
        self.assertEqual(assignments[0]["task_id"], unassigned)
        self.assertEqual(pulse_db.get_task(existing)["assignee_id"], USER_A)

    def test_no_eligible_candidates_means_no_data_changes(self):
        task_id = pulse_db.create_task("Unassigned", "", None, "", "1", "Leitung")
        self.assertEqual(pulse_db.auto_distribute_unassigned_tasks([], "900", "Leitung"), [])
        self.assertIsNone(pulse_db.get_task(task_id)["assignee_id"])
        self.assertEqual(pulse_db.task_history(task_id), [])


class WorkloadDistributionRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.role = SimpleNamespace(id=int(TEAM_ROLE), name="Team")
        self.member = SimpleNamespace(id=int(USER_A), display_name="Alice", name="Alice", roles=[self.role], bot=False)
        self.guild = SimpleNamespace(members=[self.member], roles=[self.role])
        self.manager = SimpleNamespace(
            user={"id": "900", "global_name": "Leitung"},
            perms={"can_manage_tasks": True, "can_view_dashboard": True, "can_promote": False, "is_admin": False},
            guild=self.guild,
            config={"team_role_ids": [TEAM_ROLE]},
        )
        self.viewer = SimpleNamespace(
            user={"id": "901", "global_name": "Viewer"},
            perms={"can_manage_tasks": False, "can_view_dashboard": True, "can_promote": False, "is_admin": False},
            guild=self.guild,
            config={"team_role_ids": [TEAM_ROLE]},
        )
        self.app = FastAPI()
        pulse_features.register(self.app)
        self.client = TestClient(self.app, raise_server_exceptions=False, headers={"Origin": "http://testserver"})

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_manager_can_distribute_only_to_configured_team_members(self):
        task_id = pulse_db.create_task("Support-Aufgabe", "", None, "", "900", "Leitung")
        with patch.object(ws, "auth", return_value=self.manager), patch.object(ws, "log_audit") as audit:
            response = self.client.post("/tasks/distribute", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("t=ok", response.headers["location"])
        self.assertEqual(pulse_db.get_task(task_id)["assignee_id"], USER_A)
        audit.assert_called_once()

    def test_view_only_member_cannot_trigger_distribution(self):
        task_id = pulse_db.create_task("Support-Aufgabe", "", None, "", "900", "Leitung")
        with patch.object(ws, "auth", return_value=self.viewer):
            response = self.client.post("/tasks/distribute", follow_redirects=False)
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(pulse_db.get_task(task_id)["assignee_id"])


if __name__ == "__main__":
    unittest.main()
