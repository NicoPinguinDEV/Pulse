"""Audit #108: Quizverwaltung (Fragen bearbeiten/ordnen/löschen, Einstellungen, Auswertung je Frage, Rechte; temp DB, ohne Discord)."""
import unittest

import pulse_db
import pulse_quiz_admin as qa
from tests.test_wiki_plus import Base as WikiBase

MANAGER = {"can_view_dashboard": True, "can_manage_training": True}
TEAM = {"can_view_dashboard": True}
QS = [{"question": "Was ist 1+1?", "options": ["1", "2", "3"], "answer_index": 1},
      {"question": "Hauptstadt?", "options": ["Bochum", "Berlin"], "answer_index": 1}]


class ModuleTests(WikiBase):
    def setUp(self):
        super().setUp()
        self.tid = pulse_db.create_training("Funk", "Beschreibung", 60, 20, "9", "Lead", QS)

    def questions(self):
        return pulse_db.get_training(self.tid)["questions"]

    def test_clean_question_validates(self):
        self.assertEqual(qa.clean_question(" Frage? ", ["A ", " B"], "1"), ("Frage?", ["A", "B"], 1))
        for bad in (("", ["A", "B"], 0), ("x" * 600, ["A", "B"], 0), ("F", ["A"], 0), ("F", ["A"] * 9, 0), ("F", ["A", "a"], 0), ("F", ["A", "B"], 2),
                    ("F", ["A", "B"], "x"), ("F", ["A", "B"], -1)):
            with self.assertRaises(ValueError, msg=str(bad)):
                qa.clean_question(*bad)

    def test_add_update_delete_and_order(self):
        qid = qa.add_question(self.tid, "Neue Frage", ["A", "B", "C"], 2)
        self.assertEqual([q["question"] for q in self.questions()], ["Was ist 1+1?", "Hauptstadt?", "Neue Frage"])
        qa.update_question(self.tid, qid, "Geänderte Frage", ["X", "Y"], 0)
        self.assertEqual((self.questions()[2]["question"], self.questions()[2]["options"], self.questions()[2]["answer_index"]), ("Geänderte Frage", ["X", "Y"], 0))
        qa.move_question(self.tid, qid, "up")
        self.assertEqual([q["question"] for q in self.questions()], ["Was ist 1+1?", "Geänderte Frage", "Hauptstadt?"])
        qa.move_question(self.tid, qid, "up")
        qa.move_question(self.tid, qid, "up")            # am Anfang: bleibt stehen
        self.assertEqual(self.questions()[0]["id"], qid)
        qa.delete_question(self.tid, qid)
        self.assertEqual([q["position"] for q in self.questions()], [0, 1])

    def test_last_question_cannot_be_deleted_and_foreign_ids_are_refused(self):
        ids = [q["id"] for q in self.questions()]
        qa.delete_question(self.tid, ids[0])
        with self.assertRaises(ValueError):
            qa.delete_question(self.tid, ids[1])
        other = pulse_db.create_training("Andere", "", 50, 10, "9", "Lead", QS)
        foreign = pulse_db.get_training(other)["questions"][0]["id"]
        for fn in (lambda: qa.update_question(self.tid, foreign, "X", ["A", "B"], 0), lambda: qa.delete_question(self.tid, foreign), lambda: qa.move_question(self.tid, foreign, "up")):
            with self.assertRaises(LookupError):
                fn()
        with self.assertRaises(LookupError):
            qa.add_question("nope", "F", ["A", "B"], 0)
        self.assertEqual(len(pulse_db.get_training(other)["questions"]), 2)

    def test_settings_are_validated_and_stored(self):
        qa.update_settings(self.tid, " Neuer Titel ", "Text", "75", "30")
        t = pulse_db.get_training(self.tid)
        self.assertEqual((t["title"], t["passing_score"], t["time_limit_minutes"]), ("Neuer Titel", 75, 30))
        for bad in (("", "", "80", "20"), ("T", "", "0", "20"), ("T", "", "101", "20"), ("T", "", "80", "0"), ("T", "", "x", "20")):
            with self.assertRaises(ValueError, msg=str(bad)):
                qa.update_settings(self.tid, *bad)
        with self.assertRaises(LookupError):
            qa.update_settings("nope", "T", "", "80", "20")

    def test_stats_per_question_and_tolerance_for_old_attempts(self):
        self.assertEqual(qa.stats(self.tid)["attempts"], 0)
        q1, q2 = [q["id"] for q in self.questions()]
        a1 = pulse_db.save_attempt(self.tid, "1", "Ann", 100, True)
        qa.record_answers(a1, self.questions(), {q1: "1", q2: "1"})
        a2 = pulse_db.save_attempt(self.tid, "2", "Bo", 50, False)
        qa.record_answers(a2, self.questions(), {q1: "0", q2: "1"})
        pulse_db.save_attempt(self.tid, "3", "Cy", 0, False)         # alter Versuch ohne Antworten
        stats = qa.stats(self.tid)
        self.assertEqual((stats["attempts"], stats["pass_rate"], stats["average_score"]), (3, 33, 50))
        by_q = {q["id"]: q["correct_rate"] for q in stats["questions"]}
        self.assertEqual((by_q[q1], by_q[q2]), (50, 100))

    def test_record_answers_never_raises_and_handles_garbage(self):
        self.assertEqual(qa.record_answers("a1", self.questions(), {"x": "abc"}), 2)
        self.assertEqual(qa.record_answers("a2", None, {}), 0)
        self.assertEqual(qa.stats(self.tid)["questions"][0]["correct_rate"], 0)

    def test_answers_table_is_part_of_the_backup(self):
        self.assertIn("training_answers", pulse_db.TABLES)


class RouteTests(WikiBase):
    def setUp(self):
        super().setUp()
        self.tid = pulse_db.create_training("Funk", "", 60, 20, "9", "Lead", QS)
        self.base = f"/training/{self.tid}/verwaltung"

    def post(self, path, **data):
        return self.client.post(path, data=data, follow_redirects=False)

    def test_team_member_is_refused_everywhere_without_writes(self):
        q = pulse_db.get_training(self.tid)["questions"][0]["id"]
        with self.as_user(TEAM):
            self.assertEqual(self.client.get(self.base).status_code, 403)
            self.assertEqual(self.post(self.base + "/frage", aktion="loeschen", question_id=q).status_code, 403)
            self.assertEqual(self.post(self.base + "/einstellungen", title="X", passing_score="50", time_limit="5").status_code, 403)
        self.assertEqual(len(pulse_db.get_training(self.tid)["questions"]), 2)
        self.assertEqual(pulse_db.get_training(self.tid)["title"], "Funk")

    def test_manager_edits_questions_and_settings_over_http(self):
        with self.as_user(MANAGER):
            html = self.client.get(self.base).text
            self.assertIn("Quizverwaltung", html)
            self.assertIn("noch keine Antworten", html)
            self.assertEqual(self.post(self.base + "/frage", aktion="neu", question="Neu?", options="A\nB\nC", answer_no="3").status_code, 303)
            new = pulse_db.get_training(self.tid)["questions"][-1]
            self.assertEqual((new["question"], new["options"], new["answer_index"]), ("Neu?", ["A", "B", "C"], 2))
            self.post(self.base + "/frage", aktion="speichern", question_id=new["id"], question="Neu bearbeitet?", options="A\nB", answer_no="1")
            self.post(self.base + "/frage", aktion="hoch", question_id=new["id"])
            self.assertEqual(pulse_db.get_training(self.tid)["questions"][1]["id"], new["id"])
            self.post(self.base + "/frage", aktion="loeschen", question_id=new["id"])
            self.post(self.base + "/einstellungen", title="Funk neu", description="d", passing_score="70", time_limit="15")
        t = pulse_db.get_training(self.tid)
        self.assertEqual((len(t["questions"]), t["title"], t["passing_score"]), (2, "Funk neu", 70))

    def test_invalid_input_unknown_ids_and_escaping(self):
        with self.as_user(MANAGER):
            self.assertIn("err", self.post(self.base + "/frage", aktion="neu", question="", options="A\nB", answer_no="1").headers["location"])
            self.assertIn("err", self.post(self.base + "/frage", aktion="neu", question="F", options="Nur eine", answer_no="1").headers["location"])
            self.assertIn("err", self.post(self.base + "/einstellungen", title="T", passing_score="500", time_limit="5").headers["location"])
            self.assertEqual(self.post(self.base + "/frage", aktion="speichern", question_id="gibtesnicht", question="F", options="A\nB", answer_no="1").status_code, 404)
            self.assertEqual(self.post(self.base + "/frage", aktion="tanzen").status_code, 400)
            self.assertEqual(self.client.get("/training/nope/verwaltung").status_code, 404)
            qa.add_question(self.tid, "<script>x</script>", ["<b>A</b>", "B"], 0)
            html = self.client.get(self.base).text
        self.assertNotIn("<script>x</script>", html)
        self.assertNotIn("<b>A</b>", html)
        self.assertEqual(len(pulse_db.get_training(self.tid)["questions"]), 3)       # nur die gültige, direkt angelegte Frage kam dazu

    def test_submitting_a_quiz_records_the_answers_for_the_statistics(self):
        q1, q2 = [q["id"] for q in pulse_db.get_training(self.tid)["questions"]]
        with self.as_user(TEAM, "1"):
            resp = self.client.post(f"/training/{self.tid}/submit", data={f"q_{q1}": "1", f"q_{q2}": "0"})
            self.assertEqual(resp.status_code, 200)
        stats = qa.stats(self.tid)
        self.assertEqual(stats["attempts"], 1)
        self.assertEqual({q["id"]: q["correct_rate"] for q in stats["questions"]}, {q1: 100, q2: 0})
        with self.as_user(MANAGER, "1"):
            self.assertIn("Quizverwaltung", self.client.get(f"/training/{self.tid}").text)
        with self.as_user(TEAM, "1"):
            self.assertNotIn("Quizverwaltung", self.client.get(f"/training/{self.tid}").text)


if __name__ == "__main__":
    unittest.main()
