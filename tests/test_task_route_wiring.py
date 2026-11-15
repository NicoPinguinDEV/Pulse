"""Audit #124: /tasks/update nutzt die gemeinsame Regel (pulse_task_actions) - Rechte, Ereignis, keine Schreibwirkung bei Verweigerung."""
import unittest

import pulse_db
import pulse_events as events
from tests.test_teamakte_pages import LEAD, TEAM, Base


class TaskRouteWiringTests(Base):
    def _task(self, assignee="2"):
        return pulse_db.create_task("Akte prüfen", "", assignee, "Bo", "900000000000000001", "Chef")

    def _events(self, tid):
        return events.history(correlation_id=f"task:{tid}", perms={"is_admin": True}, limit=20)

    def test_manager_changes_priority_and_event_is_written(self):
        tid = self._task()
        with self.as_user(LEAD, "1"):
            resp = self.client.post("/tasks/update", data={"task_id": tid, "status": "in_progress", "priority": "high"}, follow_redirects=False)
        self.assertLess(resp.status_code, 400)
        task = pulse_db.get_task(tid)
        self.assertEqual((task["status"], task["priority"]), ("in_progress", "high"))
        self.assertTrue(self._events(tid))

    def test_assignee_may_only_change_status(self):
        tid = self._task(assignee="2")
        with self.as_user(TEAM, "2"):
            resp = self.client.post("/tasks/update", data={"task_id": tid, "status": "done", "priority": "urgent"}, follow_redirects=False)
        self.assertLess(resp.status_code, 400)
        task = pulse_db.get_task(tid)
        self.assertEqual(task["status"], "done")
        self.assertNotEqual(task["priority"], "urgent")

    def test_stranger_is_refused_without_any_write(self):
        tid = self._task(assignee="2")
        with self.as_user(TEAM, "3"):
            resp = self.client.post("/tasks/update", data={"task_id": tid, "status": "done"}, follow_redirects=False)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(pulse_db.get_task(tid)["status"], "open")
        self.assertFalse(self._events(tid))

    def test_invalid_status_is_rejected(self):
        tid = self._task()
        with self.as_user(LEAD, "1"):
            resp = self.client.post("/tasks/update", data={"task_id": tid, "status": "kaputt"}, follow_redirects=False)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(pulse_db.get_task(tid)["status"], "open")


if __name__ == "__main__":
    unittest.main()
