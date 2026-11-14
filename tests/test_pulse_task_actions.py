"""Audit #124: one task update rule for bot and dashboard (rights, events, notifications)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_eventbus as bus
import pulse_task_actions as ta

CREATOR, ASSIGNEE, OTHER, MANAGER = "100000000000000001", "100000000000000002", "100000000000000003", "100000000000000004"
MGR = {"can_manage_tasks": True}
LEAD = {"can_promote": True}
ADMIN = {"is_admin": True}
PLAIN = {"can_view_dashboard": True}


class TaskActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.patch.start()
        pulse_db._SCHEMA_READY.discard(self.db_path)
        pulse_db.init_db()
        self.tid = pulse_db.create_task("Dienstplan prüfen", "Details", ASSIGNEE, "Anna", CREATOR, "Chef", "normal")

    def tearDown(self):
        pulse_db._SCHEMA_READY.discard(self.db_path)
        self.patch.stop()
        self.tmp.cleanup()

    def run_update(self, perms, actor, **kw):
        return ta.update(perms, actor, "Akteur", self.tid, **kw)

    def test_assignee_completes_own_task_with_history_event_and_creator_notice(self):
        cursor = bus.poll(ADMIN)["cursor"]
        res = self.run_update(PLAIN, ASSIGNEE, status="done", source="discord")
        self.assertEqual((res["ok"], res["code"]), (True, "ok"))
        self.assertEqual(pulse_db.get_task(self.tid)["status"], "done")
        self.assertEqual(pulse_db.task_history(self.tid)[0]["note"], "Per Discord geändert")
        events = bus.poll(ADMIN, cursor)["events"]
        self.assertEqual([(e["domain"], e["action"], e["correlation_id"]) for e in events],
                         [("task", "completed", f"task:{self.tid}")])
        notes = pulse_db.notifications(CREATOR)
        self.assertEqual(len(notes), 1)
        self.assertIn("erledigt", notes[0]["title"].lower())
        self.assertEqual(pulse_db.notifications(ASSIGNEE), [])  # the actor is never notified

    def test_leadership_with_can_promote_is_manager_like_on_the_web(self):
        # Before: web allowed can_promote, the bot command refused it. One rule now: allowed.
        res = self.run_update(LEAD, MANAGER, priority="urgent")
        self.assertTrue(res["ok"])
        self.assertEqual(pulse_db.get_task(self.tid)["priority"], "urgent")

    def test_stranger_and_plain_member_are_refused_and_nothing_changes(self):
        cursor = bus.poll(ADMIN)["cursor"]
        for perms in (PLAIN, {}, None):
            res = self.run_update(perms, OTHER, status="done")
            self.assertEqual(res["code"], "forbidden")
        self.assertEqual(pulse_db.get_task(self.tid)["status"], "open")
        self.assertEqual(bus.poll(ADMIN, cursor)["events"], [])
        self.assertEqual(pulse_db.notifications(CREATOR), [])

    def test_assignee_may_only_change_status(self):
        for kw in (dict(priority="urgent"), dict(due_at="2026-12-01"), dict(assignee_id=OTHER, assignee_name="X"),
                   dict(status="done", priority="low")):
            res = self.run_update(PLAIN, ASSIGNEE, **kw)
            self.assertEqual(res["code"], "forbidden", kw)
        task = pulse_db.get_task(self.tid)
        self.assertEqual((task["status"], task["priority"], task["assignee_id"]), ("open", "normal", ASSIGNEE))

    def test_reassignment_notifies_new_assignee_only(self):
        res = self.run_update(MGR, MANAGER, assignee_id=OTHER, assignee_name="Ben")
        self.assertTrue(res["ok"])
        self.assertEqual(pulse_db.get_task(self.tid)["assignee_id"], OTHER)
        self.assertEqual(len(pulse_db.notifications(OTHER)), 1)
        self.assertEqual(pulse_db.notifications(ASSIGNEE), [])
        self.assertEqual(pulse_db.notifications(MANAGER), [])
        self.assertEqual(bus.poll(ADMIN, 0)["events"][-1]["action"], "reassigned")

    def test_self_assignment_by_manager_does_not_notify_self(self):
        self.assertTrue(self.run_update(MGR, MANAGER, assignee_id=MANAGER, assignee_name="Mgr")["ok"])
        self.assertEqual(pulse_db.notifications(MANAGER), [])

    def test_validation(self):
        bad = [dict(status="finished"), dict(priority="asap"), dict(due_at="x" * 41), dict(assignee_id="abc")]
        for kw in bad:
            self.assertEqual(self.run_update(ADMIN, MANAGER, **kw)["code"], "invalid", kw)
        self.assertEqual(pulse_db.get_task(self.tid)["status"], "open")

    def test_unchanged_and_not_found(self):
        self.assertEqual(self.run_update(ADMIN, MANAGER, status="open")["code"], "unchanged")
        self.assertEqual(ta.update(ADMIN, MANAGER, "x", "no_such_task", status="done")["code"], "not_found")
        self.assertEqual(ta.update(ADMIN, MANAGER, "x", None, status="done")["code"], "not_found")

    def test_done_twice_is_unchanged_and_no_second_notification(self):
        self.assertTrue(self.run_update(PLAIN, ASSIGNEE, status="done")["ok"])
        self.assertEqual(self.run_update(PLAIN, ASSIGNEE, status="done")["code"], "unchanged")
        self.assertEqual(len(pulse_db.notifications(CREATOR)), 1)

    def test_broken_notification_does_not_undo_the_change(self):
        def broken(*a, **k):
            raise RuntimeError("inbox kaputt")
        with self.assertLogs("pulse.inbox", level="WARNING"):
            res = self.run_update(PLAIN, ASSIGNEE, status="done", notifier=broken)
        self.assertTrue(res["ok"])
        self.assertEqual(pulse_db.get_task(self.tid)["status"], "done")

    def test_policy_task_manage_matches_web_rule(self):
        for perms in (MGR, LEAD, ADMIN):
            self.assertTrue(ta.may_manage(perms))
        for perms in (PLAIN, {"can_manage_tickets": True}, {}, None):
            self.assertFalse(ta.may_manage(perms))


if __name__ == "__main__":
    unittest.main()
