"""BOT-11: on-/offboarding checklists – created once, auto-ticked steps, mentor, dashboard rights."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_checklists as pc
import pulse_db
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json"))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class ChecklistTests(TempDb):
    def test_one_open_checklist_per_person_and_kind(self):
        first = pc.start(7, "Max", "onboarding")
        self.assertEqual(pc.start(7, "Max", "onboarding")["id"], first["id"])
        self.assertNotEqual(pc.start(7, "Max", "offboarding")["id"], first["id"])
        with self.assertRaises(ValueError):
            pc.start(7, "Max", "bogus")

    def test_offboarding_ticks_what_pulse_already_did(self):
        item = pc.start(7, "Max", "offboarding", {"roles_removed": True, "tickets": False, "shift": True, "unknown": True})
        done = {s["key"] for s in item["steps"] if s["done"]}
        self.assertEqual(done, {"roles_removed", "shift"})
        self.assertEqual(item["progress"], 2)

    def test_toggle_until_complete(self):
        item = pc.start(7, "Max", "onboarding")
        for key, _ in pc.ONBOARDING:
            item = pc.toggle(item["id"], key, "Lena")
        self.assertIsNotNone(item["completed_at"])
        self.assertEqual(pc.list_open(), [])
        self.assertEqual(len(pc.list_done()), 1)
        item = pc.toggle(item["id"], "rules", "Lena")              # unticking reopens it
        self.assertIsNone(item["completed_at"])
        with self.assertRaises(ValueError):
            pc.toggle(item["id"], "nope", "Lena")

    def test_mentor_ticks_step_and_notifies(self):
        item = pc.start(7, "Max", "onboarding")
        item = pc.set_mentor(item["id"], 3, "Lena", "Admin")
        self.assertEqual(item["mentor_name"], "Lena")
        self.assertTrue(next(s for s in item["steps"] if s["key"] == "mentor")["done"])
        self.assertTrue(any(n["title"] == "🤝 Du bist Mentor" for n in pulse_db.notifications("3")))
        with self.assertRaises(ValueError):
            pc.set_mentor(pc.start(7, "Max", "offboarding")["id"], 3, "Lena", "Admin")

    def test_wired_into_the_existing_join_and_leave_paths(self):
        source = (Path(pc.__file__).parent / "cogs" / "pulse_suite.py").read_text(encoding="utf-8")
        self.assertIn('pulse_checklists.start(after.id,after.display_name,"onboarding")', source)
        self.assertIn('pulse_checklists.start(after.id,after.display_name,"offboarding"', source)


class PageTests(TempDb):
    def test_rights_toggle_and_mentor(self):
        item = pc.start(7, "Max", "onboarding")
        lena = SimpleNamespace(id=3, display_name="Lena", bot=False, roles=[SimpleNamespace(id=5)])
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(client.get("/team/checklists").status_code, 403)
        admin = fake_auth_for(ADMIN)

        def with_guild(*a, **k):
            ctx = admin(*a, **k)
            ctx.guild = SimpleNamespace(**{**vars(ctx.guild), "members": [lena], "get_member": lambda uid: lena if uid == 3 else None})
            ctx.config = {"team_role_ids": [5]}
            return ctx
        with patch.object(ws, "auth", with_guild):
            page = client.get("/team/checklists")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Onboarding · Max", page.text)
            self.assertIn("Lena", page.text)
            client.post("/team/checklists/toggle", data={"checklist_id": item["id"], "step": "roles"}, follow_redirects=False)
            bad = client.post("/team/checklists/mentor", data={"checklist_id": item["id"], "mentor_id": "99"}, follow_redirects=False)
            self.assertIn("t=err", bad.headers["location"])                # not a team member
            done = client.post("/team/checklists/mentor", data={"checklist_id": item["id"], "mentor_id": "3"}, follow_redirects=False)
            self.assertIn("t=ok", done.headers["location"])
        item = pc.get(item["id"])
        self.assertEqual(item["progress"], 2)


if __name__ == "__main__":
    unittest.main()
