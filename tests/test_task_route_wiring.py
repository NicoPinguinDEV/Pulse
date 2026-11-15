"""Audit #124: /tasks/update nutzt die gemeinsame Regel (pulse_task_actions) - Rechte, Ereignis, keine Schreibwirkung bei Verweigerung."""
import unittest

import pulse_db
import pulse_events as events
from tests.fakes import FakeMember
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

    def test_create_route_uses_the_shared_rule_event_and_notification(self):
        with self.as_user(TEAM, "3"):
            self.assertEqual(self.client.post("/tasks/create", data={"title": "X"}, follow_redirects=False).status_code, 403)
        self.assertEqual(pulse_db.list_tasks(limit=20), [])
        self.members.append(FakeMember(900000000000000002, "Dee", roles=self.members[0].roles))
        with self.as_user(LEAD, "1"):
            resp = self.client.post("/tasks/create", data={"title": "Neue Aufgabe", "assignee_id": "900000000000000002", "priority": "urgent"}, follow_redirects=False)
        self.assertEqual(resp.status_code, 303)
        task = pulse_db.list_tasks(limit=20)[0]
        self.assertEqual((task["title"], task["priority"], str(task["assignee_id"])), ("Neue Aufgabe", "urgent", "900000000000000002"))
        self.assertEqual(len(self._events(task["id"])), 1)
        self.assertTrue(any("Neue Aufgabe" in n["title"] for n in pulse_db.notifications("900000000000000002")))

    def test_create_rejects_an_empty_title(self):
        with self.as_user(LEAD, "1"):
            resp = self.client.post("/tasks/create", data={"title": "   "}, follow_redirects=False)
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(pulse_db.list_tasks(limit=20), [])


if __name__ == "__main__":
    unittest.main()
