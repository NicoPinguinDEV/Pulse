"""Learning paths #107: role-scoped sequences use passed quiz attempts as their progress source."""
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


ROLE_ID = "1531303733311307997"
OTHER_ROLE_ID = "1532762237284913363"


class FakeGuild:
    def __init__(self, roles, member):
        self.roles = roles
        self.member = member

    def get_role(self, role_id):
        return next((role for role in self.roles if int(role.id) == int(role_id)), None)

    def get_member(self, user_id):
        return self.member if int(self.member.id) == int(user_id) else None


class TrainingLearningPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.training_one = pulse_db.create_training(
            "Support Grundlagen", "Grundlagen", 80, 20, "900", "Leitung",
            [{"question": "A?", "options": ["A", "B"], "answer_index": 0}],
        )
        self.training_two = pulse_db.create_training(
            "Ticketbearbeitung", "Tickets", 80, 20, "900", "Leitung",
            [{"question": "B?", "options": ["A", "B"], "answer_index": 1}],
        )
        self.role = SimpleNamespace(id=int(ROLE_ID), name="Supporter", position=5)
        self.other_role = SimpleNamespace(id=int(OTHER_ROLE_ID), name="Moderation", position=4)
        self.member = SimpleNamespace(id=123, roles=[self.role])
        self.guild = FakeGuild([self.role, self.other_role], self.member)
        self.manager = SimpleNamespace(
            user={"id": "900", "global_name": "Leitung", "username": "leitung"},
            perms={"can_view_dashboard": True, "can_manage_training": True},
            guild=self.guild,
            config={"team_role_ids": [ROLE_ID, OTHER_ROLE_ID]},
        )
        self.member_ctx = SimpleNamespace(
            user={"id": "123", "global_name": "Teammitglied", "username": "mitglied"},
            perms={"can_view_dashboard": True},
            guild=self.guild,
            config={"team_role_ids": [ROLE_ID, OTHER_ROLE_ID]},
        )
        self.app = FastAPI()
        pulse_features.register(self.app)
        self.client = TestClient(self.app, raise_server_exceptions=False)

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_path_is_scoped_to_role_and_items_keep_the_configured_order(self):
        path_id = pulse_db.save_training_path(
            ROLE_ID, "Supporter", "Support-Ausbildung", "Grundlagen bis Ticketarbeit",
            "900", "Leitung", [self.training_two, self.training_one, self.training_two],
        )
        paths = pulse_db.training_paths_for_roles([ROLE_ID])
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]["id"], path_id)
        self.assertEqual(
            [item["training_id"] for item in paths[0]["items"]],
            [self.training_two, self.training_one],
        )
        self.assertEqual(pulse_db.training_paths_for_roles([OTHER_ROLE_ID]), [])

    def test_saving_again_updates_one_role_path_without_duplicate_records(self):
        first = pulse_db.save_training_path(
            ROLE_ID, "Supporter", "Support 1", "", "900", "Leitung", [self.training_one],
        )
        second = pulse_db.save_training_path(
            ROLE_ID, "Supporter", "Support 2", "Aktualisiert", "901", "Leitung",
            [self.training_two, self.training_one],
        )
        paths = pulse_db.training_paths()
        self.assertEqual(first, second)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0]["title"], "Support 2")
        self.assertEqual([item["training_id"] for item in paths[0]["items"]], [self.training_two, self.training_one])

    def test_path_validation_requires_published_trainings_and_a_bounded_list(self):
        with self.assertRaisesRegex(ValueError, "1 bis 30"):
            pulse_db.save_training_path(ROLE_ID, "Supporter", "Leer", "", "900", "Leitung", [])
        with self.assertRaisesRegex(ValueError, "nicht veröffentlicht"):
            pulse_db.save_training_path(ROLE_ID, "Supporter", "Ungültig", "", "900", "Leitung", ["missing"])
        with self.assertRaisesRegex(ValueError, "Teamrollen-ID"):
            pulse_db.save_training_path("42", "Fake", "Ungültige Rolle", "", "900", "Leitung", [self.training_one])

    def test_progress_counts_only_passed_attempts_and_counts_each_training_once(self):
        path_id = pulse_db.save_training_path(
            ROLE_ID, "Supporter", "Support-Ausbildung", "", "900", "Leitung",
            [self.training_one, self.training_two],
        )
        pulse_db.save_attempt(self.training_one, "123", "Teammitglied", 50, False)
        pulse_db.save_attempt(self.training_one, "123", "Teammitglied", 100, True)
        pulse_db.save_attempt(self.training_one, "123", "Teammitglied", 100, True)
        pulse_db.save_attempt(self.training_two, "456", "Andere Person", 100, True)
        self.assertEqual(
            pulse_db.training_path_progress(path_id, "123"),
            {"completed": 1, "total": 2, "percent": 50},
        )
        self.assertEqual(
            pulse_db.training_path_progress(path_id, "456"),
            {"completed": 1, "total": 2, "percent": 50},
        )

    def test_passed_training_id_lookup_is_complete_and_can_be_scoped(self):
        pulse_db.save_attempt(self.training_one, "123", "Teammitglied", 100, True)
        pulse_db.save_attempt(self.training_two, "123", "Teammitglied", 60, False)
        pulse_db.save_attempt(self.training_two, "456", "Andere Person", 100, True)
        self.assertEqual(
            pulse_db.passed_training_ids_for_user("123"),
            {self.training_one},
        )
        self.assertEqual(
            pulse_db.passed_training_ids_for_user("123", [self.training_one, self.training_two]),
            {self.training_one},
        )
        self.assertEqual(
            pulse_db.passed_training_ids_for_user("123", ["unknown-training"]),
            set(),
        )

    def test_backup_restore_includes_role_path_and_ordered_items(self):
        pulse_db.save_training_path(
            ROLE_ID, "Supporter", "Support-Ausbildung", "", "900", "Leitung",
            [self.training_one, self.training_two],
        )
        backup = pulse_db.export_state()
        self.assertEqual(len(backup["training_paths"]), 1)
        self.assertEqual(len(backup["training_path_items"]), 2)
        with pulse_db.connect() as connection:
            connection.execute("DELETE FROM training_path_items")
            connection.execute("DELETE FROM training_paths")
        pulse_db.import_state(backup)
        restored = pulse_db.training_paths_for_roles([ROLE_ID])
        self.assertEqual(len(restored), 1)
        self.assertEqual(
            [item["training_id"] for item in restored[0]["items"]],
            [self.training_one, self.training_two],
        )

    def test_learning_path_manager_route_requires_permission_and_configured_role(self):
        payload = {
            "role_id": ROLE_ID,
            "title": "Support-Ausbildung",
            "description": "Support",
            "training_ids": [self.training_one, self.training_two],
        }
        with patch.object(ws, "auth", return_value=self.member_ctx):
            denied = self.client.post("/training/paths/save", data=payload, follow_redirects=False)
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(pulse_db.training_paths(), [])

        unconfigured_payload = {**payload, "role_id": "1531303733311307998"}
        with patch.object(ws, "auth", return_value=self.manager):
            rejected = self.client.post("/training/paths/save", data=unconfigured_payload, follow_redirects=False)
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(pulse_db.training_paths(), [])

    def test_manager_can_save_path_only_for_a_configured_live_discord_role(self):
        payload = {
            "role_id": ROLE_ID,
            "title": "Support-Ausbildung",
            "description": "Grundlagen bis Ticketarbeit",
            "training_ids": [self.training_one, self.training_two],
        }
        with (
            patch.object(ws, "auth", return_value=self.manager),
            patch.object(ws, "log_audit") as audit,
        ):
            response = self.client.post("/training/paths/save", data=payload, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(pulse_db.training_paths_for_roles([ROLE_ID])), 1)
        audit.assert_called_once()

    def test_training_page_shows_only_paths_for_current_member_roles(self):
        pulse_db.save_training_path(
            ROLE_ID, "Supporter", "Support-Ausbildung", "", "900", "Leitung",
            [self.training_one, self.training_two],
        )
        pulse_db.save_training_path(
            OTHER_ROLE_ID, "Moderation", "Moderationspfad", "", "900", "Leitung",
            [self.training_two],
        )
        pulse_db.save_attempt(self.training_one, "123", "Teammitglied", 100, True)
        with (
            patch.object(ws, "auth", return_value=self.member_ctx),
            patch.object(ws, "render_page", side_effect=lambda _title, _ctx, _nav, body: body),
        ):
            response = self.client.get("/training")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Support-Ausbildung", response.text)
        self.assertNotIn("Moderationspfad", response.text)
        self.assertIn("50%", response.text)
        self.assertIn("✅ Bestanden", response.text)
        self.assertIn("○ Offen", response.text)
        self.assertNotIn("/training/paths/save", response.text)

    def test_manager_can_see_and_edit_only_configured_role_paths(self):
        pulse_db.save_training_path(
            ROLE_ID, "Supporter", "Support-Ausbildung", "", "900", "Leitung",
            [self.training_one],
        )
        with (
            patch.object(ws, "auth", return_value=self.manager),
            patch.object(ws, "render_page", side_effect=lambda _title, _ctx, _nav, body: body),
        ):
            response = self.client.get("/training")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Rollen-Lernpfade verwalten", response.text)
        self.assertIn("Supporter · Bestehenden Lernpfad bearbeiten", response.text)
        self.assertIn('action="/training/paths/save"', response.text)
        self.assertIn('name="training_ids"', response.text)


if __name__ == "__main__":
    unittest.main()
