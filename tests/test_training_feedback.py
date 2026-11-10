"""Training feedback #115: eligible members can rate a training once, anonymously in aggregate."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import webserver as ws
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TrainingFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.training_id = pulse_db.create_training(
            "Support-Grundlagen",
            "Interne Schulung",
            80,
            20,
            "900",
            "Leitung",
            [{"question": "Was gilt?", "options": ["A", "B"], "answer_index": 0}],
        )
        import pulse_features
        self.app = FastAPI()
        pulse_features.register(self.app)
        self.client = TestClient(self.app, raise_server_exceptions=False)
        self.member = SimpleNamespace(
            user={"id": "123", "global_name": "Teammitglied", "username": "teammitglied"},
            perms={"can_view_dashboard": True},
        )
        self.manager = SimpleNamespace(
            user={"id": "900", "global_name": "Leitung", "username": "leitung"},
            perms={"can_view_dashboard": True, "can_manage_training": True},
        )

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_member_can_feedback_after_attempt_and_update_same_entry(self):
        pulse_db.save_attempt(self.training_id, "123", "Teammitglied", 100, True)
        with patch.object(ws, "auth", return_value=self.member), patch.object(ws, "log_audit"):
            first = self.client.post(
                f"/training/{self.training_id}/feedback",
                data={"rating": "5", "feedback": "Sehr verständlich."},
                follow_redirects=False,
            )
            self.assertEqual(first.status_code, 303)
            second = self.client.post(
                f"/training/{self.training_id}/feedback",
                data={"rating": "4", "feedback": "Bitte mehr Praxisbeispiele."},
                follow_redirects=False,
            )
        self.assertEqual(second.status_code, 303)
        stored = pulse_db.get_training_feedback(self.training_id, "123")
        self.assertEqual(stored["rating"], 4)
        self.assertEqual(stored["feedback"], "Bitte mehr Praxisbeispiele.")
        self.assertEqual(pulse_db.training_feedback_summary(self.training_id)["count"], 1)

    def test_feedback_is_rejected_without_an_attempt(self):
        with patch.object(ws, "auth", return_value=self.member), patch.object(ws, "log_audit"):
            response = self.client.post(
                f"/training/{self.training_id}/feedback",
                data={"rating": "5", "feedback": "Test"},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(pulse_db.get_training_feedback(self.training_id, "123"))

    def test_rating_and_comment_validation(self):
        pulse_db.save_attempt(self.training_id, "123", "Teammitglied", 60, False)
        with patch.object(ws, "auth", return_value=self.member), patch.object(ws, "log_audit"):
            invalid_rating = self.client.post(
                f"/training/{self.training_id}/feedback",
                data={"rating": "6", "feedback": ""},
                follow_redirects=False,
            )
            long_comment = self.client.post(
                f"/training/{self.training_id}/feedback",
                data={"rating": "3", "feedback": "x" * 501},
                follow_redirects=False,
            )
        self.assertEqual(invalid_rating.status_code, 400)
        self.assertEqual(long_comment.status_code, 400)
        self.assertIsNone(pulse_db.get_training_feedback(self.training_id, "123"))

    def test_feedback_requires_an_existing_training(self):
        with patch.object(ws, "auth", return_value=self.member), patch.object(ws, "log_audit"):
            response = self.client.post(
                "/training/missing-training/feedback",
                data={"rating": "4", "feedback": ""},
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 404)

    def test_training_page_offers_form_only_after_attempt_and_keeps_comments_private(self):
        pulse_db.save_attempt(self.training_id, "123", "Teammitglied", 100, True)
        pulse_db.save_training_feedback(self.training_id, "123", 5, "Sehr gut.")
        pulse_db.save_attempt(self.training_id, "456", "Andere Person", 80, True)
        pulse_db.save_training_feedback(self.training_id, "456", 4, "Mehr Beispiele.")

        with (
            patch.object(ws, "auth", return_value=self.member),
            patch.object(ws, "render_page", side_effect=lambda _title, _ctx, _nav, body: body),
        ):
            member_page = self.client.get(f"/training/{self.training_id}")
        self.assertEqual(member_page.status_code, 200)
        self.assertIn("4.50/5", member_page.text)
        self.assertIn("name=\"rating\"", member_page.text)
        self.assertIn("Feedback aktualisieren", member_page.text)
        self.assertNotIn("Mehr Beispiele.", member_page.text)
        self.assertNotIn("Andere Person", member_page.text)

        with (
            patch.object(ws, "auth", return_value=self.manager),
            patch.object(ws, "render_page", side_effect=lambda _title, _ctx, _nav, body: body),
        ):
            manager_page = self.client.get(f"/training/{self.training_id}")
        self.assertIn("Anonyme Rückmeldungen", manager_page.text)
        self.assertIn("Mehr Beispiele.", manager_page.text)
        self.assertNotIn("Andere Person", manager_page.text)

    def test_backup_restore_includes_training_feedback(self):
        pulse_db.save_attempt(self.training_id, "123", "Teammitglied", 100, True)
        pulse_db.save_training_feedback(self.training_id, "123", 5, "Alles klar.")
        state = pulse_db.export_state()
        self.assertEqual(len(state["training_feedback"]), 1)
        with pulse_db.connect() as connection:
            connection.execute("DELETE FROM training_feedback")
        pulse_db.import_state({**state, "training_feedback": []})
        self.assertIsNone(pulse_db.get_training_feedback(self.training_id, "123"))
        pulse_db.import_state(state)
        self.assertEqual(pulse_db.get_training_feedback(self.training_id, "123")["feedback"], "Alles klar.")


if __name__ == "__main__":
    unittest.main()
