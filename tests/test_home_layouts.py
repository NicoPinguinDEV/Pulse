"""Phase: per-user named custom dashboard layouts (#69)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_home
import pulse_home_layouts as layouts
import webserver as ws

BROWSER = {"Origin": "http://testserver"}


class HomeLayoutStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patch_db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.patch_db.start()
        pulse_db.init_db()

    def tearDown(self):
        self.patch_db.stop()
        self.tmp.cleanup()

    def test_widget_order_is_allowlisted_unique_and_user_controlled(self):
        order = layouts.ordered_widgets(
            ["inbox", "tasks", "week", "tasks", "unknown"],
            {"inbox": "3", "tasks": "1", "week": "2"},
        )
        self.assertEqual(order, ["tasks", "week", "inbox"])
        self.assertEqual(set(order), set(pulse_home.DEFAULT_HOME_WIDGETS) & set(order))

    def test_layout_create_replace_activate_and_delete(self):
        first = layouts.save_layout(
            "123", " Support-Schicht ", ["inbox", "tasks", "week"],
            columns=1, positions={"inbox": 3, "tasks": 1, "week": 2},
        )
        self.assertEqual(first["widgets"], ["tasks", "week", "inbox"])
        self.assertEqual(first["columns"], 1)
        self.assertFalse(first["replaced"])
        self.assertEqual(layouts.active_layout("123")["id"], first["id"])

        replaced = layouts.save_layout("123", "support-schicht", ["tickets"], columns=2)
        self.assertTrue(replaced["replaced"])
        self.assertEqual(replaced["id"], first["id"])
        self.assertEqual(layouts.list_layouts("123"), [{
            "id": first["id"], "name": "support-schicht", "widgets": ["tickets"], "columns": 2,
        }])

        self.assertIsNone(layouts.activate_layout("456", first["id"]))
        self.assertIsNone(layouts.delete_layout("456", first["id"]))
        self.assertEqual(layouts.active_layout("123")["id"], first["id"])
        self.assertEqual(layouts.delete_layout("123", first["id"])["id"], first["id"])
        self.assertIsNone(layouts.active_layout("123"))
        self.assertEqual(layouts.list_layouts("123"), [])

    def test_validation_and_layout_limit(self):
        for name, widgets in (("", ["week"]), ("x" * 41, ["week"]), ("Leer", [])):
            with self.assertRaises(ValueError):
                layouts.save_layout("123", name, widgets)
        for index in range(layouts.MAX_LAYOUTS):
            layouts.save_layout("123", f"Layout {index}", ["week"])
        with self.assertRaises(ValueError):
            layouts.save_layout("123", "Zu viele", ["week"])
        # A same-name update remains possible at the configured limit.
        updated = layouts.save_layout("123", "Layout 0", ["tickets"])
        self.assertTrue(updated["replaced"])
        self.assertEqual(len(layouts.list_layouts("123")), layouts.MAX_LAYOUTS)

    def test_malformed_or_foreign_stored_records_are_ignored(self):
        pulse_db.set_setting("home_custom_layouts:123", [
            {"id": "bad id", "name": "Unsafe", "widgets": ["week"]},
            {"id": "aaaaaaaaaaaa", "name": "Safe", "widgets": ["week", "unknown", "week"], "columns": 7},
            {"id": "bbbbbbbbbbbb", "name": "safe", "widgets": ["tasks"]},
            {"id": "cccccccccccc", "name": "Empty", "widgets": []},
        ])
        self.assertEqual(layouts.list_layouts("123"), [
            {"id": "aaaaaaaaaaaa", "name": "Safe", "widgets": ["week"], "columns": 2},
        ])
        self.assertEqual(layouts.list_layouts("456"), [])


class HomeLayoutRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patch_db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.patch_db.start()
        pulse_db.init_db()
        self.user_id = "123"
        self.context = SimpleNamespace(
            user={"id": self.user_id, "global_name": "Test"},
            perms={}, member=None, config={}, guild=None, guild_name="Testserver",
            msg="", msg_ok=True,
        )
        self.auth_patch = patch.object(ws, "auth", return_value=self.context)
        self.auth_patch.start()
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers=BROWSER)

    def tearDown(self):
        self.auth_patch.stop()
        self.patch_db.stop()
        self.tmp.cleanup()

    def test_routes_save_activate_delete_and_return_to_default(self):
        page = self.client.get("/me/layouts")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Eigene Dashboard-Layouts", page.text)
        saved = self.client.post("/me/layouts/save", data={
            "name": "Support", "columns": "1", "widgets": ["tasks", "inbox"],
            "position_tasks": "1", "position_inbox": "2",
        }, follow_redirects=False)
        self.assertEqual(saved.status_code, 303)
        self.assertIn("t=ok", saved.headers["location"])
        [layout] = layouts.list_layouts("123")
        self.assertEqual(layout["widgets"], ["tasks", "inbox"])
        self.assertEqual(layouts.active_layout("123")["id"], layout["id"])

        pulse_db.delete_setting(f"{layouts.ACTIVE_KEY}:123")
        other = self.client.post("/me/layouts/activate", data={"layout_id": layout["id"]}, follow_redirects=False)
        self.assertIn("t=ok", other.headers["location"])
        self.assertEqual(layouts.active_layout("123")["id"], layout["id"])

        foreign = self.client.post("/me/layouts/delete", data={"layout_id": "dddddddddddd"}, follow_redirects=False)
        self.assertIn("t=err", foreign.headers["location"])
        self.assertEqual(len(layouts.list_layouts("123")), 1)

        deleted = self.client.post("/me/layouts/delete", data={"layout_id": layout["id"]}, follow_redirects=False)
        self.assertIn("t=ok", deleted.headers["location"])
        self.assertEqual(layouts.list_layouts("123"), [])
        self.assertIsNone(layouts.active_layout("123"))

    def test_bad_layout_requests_do_not_change_saved_layouts(self):
        for data in (
            {"name": "No widgets", "columns": "2", "widgets": []},
            {"name": "x" * 100, "columns": "2", "widgets": ["week"]},
        ):
            response = self.client.post("/me/layouts/save", data=data, follow_redirects=False)
            self.assertIn("t=err", response.headers["location"])
        self.assertEqual(layouts.list_layouts("123"), [])


if __name__ == "__main__":
    unittest.main()
