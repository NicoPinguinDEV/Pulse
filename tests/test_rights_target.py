"""Audit #160: Soll/Ist der Rechte je Person und Recht (gespeicherter Soll-Stand, Abweichungen, Admin-Route)."""
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import pulse_rights_diff as rd
import webserver as ws
from tests.fakes import FakeGuild, FakeMember, FakeRole

SUPPORT, MOD = FakeRole(10, "Support"), FakeRole(11, "Moderation")
CONFIG = {"team_role_ids": [10, 11], "permissions": {"10": {"can_manage_tickets": True}, "11": {"can_warn": True}}}


class TargetBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = f"{self.tmp.name}/pulse.db"
        patcher = patch.object(pulse_db, "DB_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        pulse_db._SCHEMA_READY.discard(path)
        pulse_db.init_db()
        self.ann, self.bo = FakeMember(1, "Ann", roles=[SUPPORT]), FakeMember(2, "Bo", roles=[MOD])
        self.guild = FakeGuild(members=[self.ann, self.bo], roles=[SUPPORT, MOD])


class TargetTests(TargetBase):
    def test_no_target_means_no_findings(self):
        d = rd.person_diff(self.guild, CONFIG)
        self.assertEqual((d["has_target"], d["changed"], d["new"], d["left"]), (False, [], [], []))

    def test_saved_target_matches_until_something_changes(self):
        self.assertEqual(rd.save_target(self.guild, CONFIG, "Admin"), 2)
        d = rd.person_diff(self.guild, CONFIG)
        self.assertTrue(d["has_target"])
        self.assertEqual((d["changed"], d["new"], d["left"]), ([], [], []))

    def test_extra_missing_new_and_left_are_reported_per_right(self):
        rd.save_target(self.guild, CONFIG, "Admin")
        self.ann.roles.append(MOD)                      # Ann bekommt zusätzlich die Moderationsrechte
        self.bo.roles.remove(MOD)                       # Bo verliert seine Rolle -> kein Teammitglied mehr
        cy = FakeMember(3, "Cy", roles=[SUPPORT])       # neu im Team
        self.guild.members.append(cy)
        d = rd.person_diff(self.guild, CONFIG)
        self.assertEqual([(c["name"], c["extra"], c["missing"]) for c in d["changed"]], [("Ann", ["can_warn"], [])])
        self.assertEqual([x["user_id"] for x in d["left"]], ["2"])
        self.assertEqual([x["name"] for x in d["new"]], ["Cy"])

    def test_a_removed_right_shows_as_missing(self):
        rd.save_target(self.guild, CONFIG, "Admin")
        changed = {"team_role_ids": [10, 11], "permissions": {"10": {}, "11": {"can_warn": True}}}
        d = rd.person_diff(self.guild, changed)
        self.assertEqual([(c["name"], c["missing"]) for c in d["changed"]], [("Ann", ["can_manage_tickets"])])

    def test_garbage_in_storage_is_tolerated(self):
        pulse_db.set_setting(rd.TARGET_KEY, {"people": {"1": "kaputt", "2": ["can_warn"]}})
        self.assertEqual(rd.load_target()["people"], {"2": ["can_warn"]})
        pulse_db.set_setting(rd.TARGET_KEY, "x")
        self.assertEqual(rd.load_target()["people"], {})


class RouteTests(TargetBase):
    def _auth(self, perms):
        guild = self.guild

        def auth(request, session, perm="can_view_dashboard", admin=False):
            if admin and not perms.get("is_admin"):
                raise HTTPException(status_code=403, detail="nur Admin")
            return SimpleNamespace(user={"id": "7", "global_name": "Admin"}, guild=guild, guild_name="G", perms=perms, config=CONFIG,
                                   member=None, msg="", msg_ok=True)
        return patch.object(ws, "auth", auth)

    def test_only_admins_can_save_the_target_and_nothing_is_written_otherwise(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with self._auth({"can_view_dashboard": True, "can_promote": True}):
            self.assertEqual(client.post("/admin/betrieb/rechte-soll", follow_redirects=False).status_code, 403)
        self.assertFalse(rd.load_target()["people"])
        with self._auth({"is_admin": True}), patch.object(ws, "log_audit"):
            self.assertEqual(client.post("/admin/betrieb/rechte-soll", follow_redirects=False).status_code, 303)
        self.assertEqual(set(rd.load_target()["people"]), {"1", "2"})

    def test_tab_shows_the_person_table(self):
        client = TestClient(ws.app, raise_server_exceptions=False)
        with self._auth({"is_admin": True}):
            self.assertIn("Noch kein Soll-Stand", client.get("/admin/betrieb?ansicht=rechte").text)
            rd.save_target(self.guild, CONFIG, "Admin")
            self.ann.roles.append(MOD)
            html = client.get("/admin/betrieb?ansicht=rechte").text
        self.assertIn("Zusätzlich: can_warn", html)


if __name__ == "__main__":
    unittest.main()
