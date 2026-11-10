"""Wiki #112: every edit and restore creates a new immutable article revision."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import webserver as ws
from fastapi.testclient import TestClient


class WikiVersioningTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = Path(self.tmp.name) / "pulse.db"
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(self.db_path))
        self.db_patch.start()
        pulse_db.init_db()
        self.client = TestClient(ws.app, raise_server_exceptions=False)
        self.manager = SimpleNamespace(
            user={"id": "900", "global_name": "Leitung", "username": "leitung"},
            perms={"can_manage_wiki": True, "can_view_dashboard": True},
        )
        self.member = SimpleNamespace(
            user={"id": "123", "global_name": "Teammitglied", "username": "teammitglied"},
            perms={"can_view_dashboard": True},
        )

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_create_and_edit_keep_every_version_and_export_it(self):
        page_id = pulse_db.save_wiki("Regeln", "Allgemein", "Stand eins", "1", "Max")
        self.assertEqual(
            [(row["version_number"], row["content"]) for row in pulse_db.wiki_page_versions(page_id)],
            [(1, "Stand eins")],
        )

        pulse_db.save_wiki("Regeln aktualisiert", "Allgemein", "Stand zwei", "2", "Lena", page_id=page_id)
        versions = pulse_db.wiki_page_versions(page_id)
        self.assertEqual(
            [(row["version_number"], row["title"], row["content"]) for row in versions],
            [(2, "Regeln aktualisiert", "Stand zwei"), (1, "Regeln", "Stand eins")],
        )
        self.assertEqual(pulse_db.get_wiki(page_id)["content"], "Stand zwei")
        self.assertEqual(len(pulse_db.export_state()["wiki_page_versions"]), 2)

    def test_first_edit_of_a_legacy_page_preserves_the_pre_edit_snapshot(self):
        with pulse_db.connect() as connection:
            connection.execute(
                "INSERT INTO wiki_pages VALUES(?,?,?,?,?,?,?,?)",
                ("legacy", "Alte Regeln", "Alt", "Originaltext", "7", "Vorher", "2026-01-01", "2026-01-02"),
            )

        pulse_db.save_wiki("Neue Regeln", "Aktuell", "Überarbeitet", "8", "Editor", page_id="legacy")
        versions = pulse_db.wiki_page_versions("legacy")
        self.assertEqual(
            [(row["version_number"], row["content"]) for row in versions],
            [(2, "Überarbeitet"), (1, "Originaltext")],
        )
        self.assertEqual(versions[1]["author_name"], "Vorher")

    def test_restore_adds_a_new_revision_without_deleting_later_versions(self):
        page_id = pulse_db.save_wiki("Regeln", "Allgemein", "Version 1", "1", "Max")
        pulse_db.save_wiki("Regeln", "Allgemein", "Version 2", "2", "Lena", page_id=page_id)

        new_version = pulse_db.restore_wiki_version(page_id, 1, "3", "Leitung")
        self.assertEqual(new_version, 3)
        self.assertEqual(pulse_db.get_wiki(page_id)["content"], "Version 1")

        versions = pulse_db.wiki_page_versions(page_id)
        self.assertEqual(
            [(row["version_number"], row["content"]) for row in versions],
            [(3, "Version 1"), (2, "Version 2"), (1, "Version 1")],
        )
        self.assertEqual(versions[0]["restored_from_version"], 1)
        self.assertEqual(versions[0]["author_name"], "Leitung")

    def test_missing_page_or_revision_cannot_be_restored(self):
        page_id = pulse_db.save_wiki("Regeln", "Allgemein", "Text", "1", "Max")
        with self.assertRaisesRegex(ValueError, "nicht gefunden"):
            pulse_db.restore_wiki_version(page_id, 99, "3", "Leitung")
        with self.assertRaisesRegex(ValueError, "nicht gefunden"):
            pulse_db.restore_wiki_version("missing-page", 1, "3", "Leitung")

    def test_wiki_export_import_round_trip_includes_revision_history(self):
        page_id = pulse_db.save_wiki("Regeln", "Allgemein", "Version 1", "1", "Max")
        pulse_db.save_wiki("Regeln", "Allgemein", "Version 2", "2", "Lena", page_id=page_id)
        backup = pulse_db.export_state()
        pulse_db.delete_wiki(page_id)
        pulse_db.import_state(backup)
        self.assertEqual(pulse_db.get_wiki(page_id)["content"], "Version 2")
        self.assertEqual([row["content"] for row in pulse_db.wiki_page_versions(page_id)], ["Version 2", "Version 1"])

    def test_detail_page_displays_history_and_manager_edit_form(self):
        page_id = pulse_db.save_wiki("Regeln", "Allgemein", "<Script>alt</Script>", "1", "Max")
        pulse_db.save_wiki("Regeln", "Allgemein", "Neue Fassung", "2", "Lena", page_id=page_id)
        with (
            patch.object(ws, "auth", return_value=self.manager),
            patch.object(ws, "render_page", side_effect=lambda _title, _ctx, _nav, body: body),
        ):
            response = self.client.get(f"/wiki/{page_id}")

        self.assertEqual(response.status_code, 200)
        self.assertIn("Versionsverlauf", response.text)
        self.assertIn("Version 1", response.text)
        self.assertIn("Version 2", response.text)
        self.assertIn("&lt;Script&gt;alt&lt;/Script&gt;", response.text)
        self.assertIn('name="page_id"', response.text)
        self.assertIn("Neue Version speichern", response.text)

    def test_restore_route_requires_wiki_management_permission(self):
        page_id = pulse_db.save_wiki("Regeln", "Allgemein", "Version 1", "1", "Max")
        pulse_db.save_wiki("Regeln", "Allgemein", "Version 2", "2", "Lena", page_id=page_id)
        with (
            patch.object(ws, "auth", return_value=self.member),
            patch.object(ws, "log_audit"),
        ):
            denied = self.client.post(
                f"/wiki/{page_id}/restore/1",
                follow_redirects=False,
            )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(pulse_db.get_wiki(page_id)["content"], "Version 2")

    def test_manager_can_restore_a_revision_through_the_route(self):
        page_id = pulse_db.save_wiki("Regeln", "Allgemein", "Version 1", "1", "Max")
        pulse_db.save_wiki("Regeln", "Allgemein", "Version 2", "2", "Lena", page_id=page_id)
        with (
            patch.object(ws, "auth", return_value=self.manager),
            patch.object(ws, "log_audit") as audit,
        ):
            response = self.client.post(
                f"/wiki/{page_id}/restore/1",
                follow_redirects=False,
            )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(pulse_db.get_wiki(page_id)["content"], "Version 1")
        self.assertEqual(pulse_db.wiki_page_versions(page_id)[0]["version_number"], 3)
        audit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
