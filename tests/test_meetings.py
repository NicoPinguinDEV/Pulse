"""Audit #51: Besprechungsmodus (Ablauf, Rechte, Aufgaben aus Beschlüssen, Protokollsperre, Archivkopie; temp DB, keine Discord-Verbindung)."""
import unittest

import pulse_db
import pulse_meetings as mt
from tests.fakes import FakeMember
from tests.test_wiki_plus import Base as WikiBase

LEAD = {"can_view_dashboard": True, "can_promote": True, "can_manage_tasks": True}
TEAM = {"can_view_dashboard": True}
A, B, C = "900000000000000001", "900000000000000002", "900000000000000003"
PEOPLE = [(A, "Ann"), (B, "Bo")]


class FlowTests(WikiBase):
    def meeting(self, **kw):
        return mt.create("Teamsitzung", "2026-11-20T18:00", kw.get("agenda", ["Lage", "Ausblick"]), kw.get("people", PEOPLE), facilitator_id=A, facilitator_name="Ann")

    def test_create_validates_and_stores_agenda_and_participants(self):
        m = self.meeting()
        self.assertEqual(([a["title"] for a in m["agenda"]], [p["attendance"] for p in m["participants"]], m["status"]), (["Lage", "Ausblick"], ["invited", "invited"], "planned"))
        for bad in (dict(title=""), dict(title="x" * 200), dict(agenda=[]), dict(agenda=[" ", ""]), dict(agenda=["x"] * 21)):
            args = dict(title="T", agenda=["A"])
            args.update(bad)
            with self.assertRaises(ValueError, msg=str(bad)):
                mt.create(args["title"], "", args["agenda"], PEOPLE)

    def test_non_numeric_participants_and_duplicates_are_dropped(self):
        m = mt.create("T", "", ["A"], [("abc", "X"), (A, "Ann"), (A, "Ann zwei")])
        self.assertEqual([p["user_id"] for p in m["participants"]], [A])

    def test_decisions_only_in_a_running_meeting_and_protocol_locks_when_closed(self):
        m = self.meeting()
        self.assertEqual(mt.add_decision(m["id"], "Zu früh")[1], "not_running")
        self.assertEqual(mt.start(m["id"]), (True, "ok"))
        self.assertEqual(mt.start(m["id"]), (False, "wrong_state"))
        self.assertEqual(mt.set_attendance(m["id"], A, "present"), (True, "ok"))
        self.assertEqual(mt.set_attendance(m["id"], C, "present"), (False, "unknown_participant"))
        self.assertEqual(mt.set_attendance(m["id"], A, "vielleicht"), (False, "invalid"))
        ok, code, decision = mt.add_decision(m["id"], "Wir erhöhen die Besetzung am Freitag", actor_id=A, actor_name="Ann")
        self.assertEqual((ok, code, decision["task_id"]), (True, "ok", ""))
        self.assertEqual(mt.add_decision(m["id"], "  ")[1], "invalid")
        self.assertEqual(mt.close(m["id"]), (True, "ok"))
        self.assertEqual(mt.close(m["id"]), (False, "wrong_state"))
        self.assertEqual(mt.add_decision(m["id"], "Nachträglich")[1], "not_running")
        self.assertEqual(mt.set_attendance(m["id"], B, "present"), (False, "closed"))
        self.assertEqual(len(mt.get(m["id"])["decisions"]), 1)

    def test_concurrent_close_does_not_archive_a_second_summary(self):
        meeting = self.meeting()
        mt.start(meeting["id"])
        stale = mt.get(meeting["id"])
        with pulse_db.connect() as db:
            db.execute(
                "UPDATE meeting_sessions SET status='closed',closed_at=? WHERE id=? AND status='running'",
                ("2026-11-20T18:01:00+00:00", meeting["id"]),
            )
        from unittest.mock import patch
        with patch.object(mt, "get", return_value=stale):
            self.assertEqual(mt.close(meeting["id"]), (False, "wrong_state"))
        self.assertEqual(pulse_db.meeting_history(10), [])

    def test_closing_archives_a_summary_in_the_existing_meeting_history(self):
        m = self.meeting()
        mt.start(m["id"])
        mt.set_attendance(m["id"], A, "present")
        mt.add_decision(m["id"], "Beschluss eins", actor_id=A, actor_name="Ann")
        mt.close(m["id"])
        [entry] = pulse_db.meeting_history(10)
        self.assertEqual(entry["title"], "Teamsitzung")
        self.assertIn("Beschluss eins", entry["notes"])

    def test_decision_with_task_uses_the_shared_task_rule_and_links_it(self):
        m = self.meeting()
        mt.start(m["id"])
        task = {"assignee_id": int(B), "assignee_name": "Bo", "title": "Dienstplan anpassen", "priority": "high"}
        ok, code, decision = mt.add_decision(m["id"], "Dienstplan wird angepasst", actor_id=A, actor_name="Ann", task=task, perms=LEAD)
        self.assertEqual((ok, code), (True, "ok"))
        stored = pulse_db.get_task(decision["task_id"])
        self.assertEqual((stored["title"], stored["priority"], str(stored["assignee_id"])), ("Dienstplan anpassen", "high", B))
        self.assertIn("Teamsitzung", stored["description"])

    def test_a_task_without_the_task_right_creates_neither_task_nor_decision(self):
        m = self.meeting()
        mt.start(m["id"])
        ok, code, _ = mt.add_decision(m["id"], "Beschluss", actor_id=A, actor_name="Ann", task={"assignee_id": int(B), "title": "X"}, perms=TEAM)
        self.assertEqual((ok, code), (False, "forbidden"))
        self.assertEqual(pulse_db.list_tasks(limit=20), [])
        self.assertEqual(mt.get(m["id"])["decisions"], [])

    def test_unknown_meeting_and_backup_tables(self):
        self.assertIsNone(mt.get("nope"))
        self.assertEqual(mt.start("nope"), (False, "not_found"))
        self.assertEqual(mt.close("nope"), (False, "not_found"))
        self.assertTrue({"meeting_sessions", "meeting_agenda", "meeting_participants", "meeting_decisions"} <= set(pulse_db.TABLES))


class RouteTests(WikiBase):
    def setUp(self):
        super().setUp()
        self.members += [FakeMember(int(A), "Ann", roles=[self.role]), FakeMember(int(B), "Bo", roles=[self.role])]

    def post(self, path, **data):
        return self.client.post(path, data=data, follow_redirects=False)

    def test_team_can_read_but_not_create_or_lead(self):
        with self.as_user(LEAD, A):
            self.post("/besprechungen/neu", title="Sitzung", agenda="Punkt 1\nPunkt 2", alle="1")
        [m] = mt.list_meetings()
        with self.as_user(TEAM, B):
            self.assertIn("Sitzung", self.client.get("/besprechungen").text)
            self.assertIn("Punkt 2", self.client.get(f"/besprechungen/{m['id']}").text)
            self.assertNotIn("Besprechung starten", self.client.get(f"/besprechungen/{m['id']}").text)
            for path, data in (("/besprechungen/neu", {"title": "X", "agenda": "Y"}), (f"/besprechungen/{m['id']}/start", {}),
                               (f"/besprechungen/{m['id']}/beschluss", {"text": "X"}), (f"/besprechungen/{m['id']}/abschliessen", {})):
                self.assertEqual(self.post(path, **data).status_code, 403, path)
        self.assertEqual(mt.get(m["id"])["status"], "planned")

    def test_full_flow_over_http_with_task_and_notifications(self):
        with self.as_user(LEAD, A):
            self.assertEqual(self.post("/besprechungen/neu", title="Sitzung", agenda="Lage", alle="1").status_code, 303)
            [m] = mt.list_meetings()
            self.assertEqual(self.post(f"/besprechungen/{m['id']}/start").status_code, 303)
            self.post(f"/besprechungen/{m['id']}/anwesenheit?status=present", user_id=B)
            self.post(f"/besprechungen/{m['id']}/beschluss", text="Aufgabe verteilen", assignee_id=B, task_title="Plan prüfen")
            self.assertEqual(self.post(f"/besprechungen/{m['id']}/abschliessen").status_code, 303)
        full = mt.get(m["id"])
        self.assertEqual(full["status"], "closed")
        self.assertTrue(full["decisions"][0]["task_id"])
        self.assertTrue(any("Sitzung" in n["title"] for n in pulse_db.notifications(B)))      # Einladung und/oder Protokoll

    def test_invalid_inputs_are_refused_without_writes(self):
        with self.as_user(LEAD, A):
            self.assertEqual(self.post("/besprechungen/neu", title="", agenda="x").status_code, 422)
            self.assertIn("err", self.post("/besprechungen/neu", title="T", agenda="  ").headers["location"])
            self.assertEqual(mt.list_meetings(), [])
            self.assertEqual(self.client.get("/besprechungen/gibtesnicht").status_code, 404)
            self.assertEqual(self.post("/besprechungen/gibtesnicht/start").status_code, 404)
            self.post("/besprechungen/neu", title="T", agenda="A", alle="1")
            [m] = mt.list_meetings()
            self.post(f"/besprechungen/{m['id']}/start")
            res = self.post(f"/besprechungen/{m['id']}/beschluss", text="Beschluss", assignee_id="12345")     # unbekannte Person
            self.assertIn("err", res.headers["location"])
            self.assertEqual(mt.get(m["id"])["decisions"], [])

    def test_html_is_escaped(self):
        with self.as_user(LEAD, A):
            self.post("/besprechungen/neu", title="<script>x</script>", agenda="<b>Punkt</b>", alle="1")
            [m] = mt.list_meetings()
            html = self.client.get(f"/besprechungen/{m['id']}").text + self.client.get("/besprechungen").text
        self.assertNotIn("<script>x</script>", html)
        self.assertNotIn("<b>Punkt</b>", html)


if __name__ == "__main__":
    unittest.main()

