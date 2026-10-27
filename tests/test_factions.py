"""Fraktionsverwaltung: data model, access (role or admin only, deny by default) and the dashboard routes.

Runs the real FastAPI routes with a fake login (no Discord connection, temp database and audit file).
"""
import json
import sqlite3
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import pulse_factions as pf
import pulse_permissions
import webserver as ws
from pulse_ids import FACTION_MANAGER_ROLE_ID
from tests.test_pulse_admin_area import ADMIN, TEAMLER

TEAM_ROLE = 100


def person(uid, name, *role_ids, bot=False):
    return SimpleNamespace(id=uid, display_name=name, name=name.lower(), global_name=name, bot=bot,
                           roles=[SimpleNamespace(id=r, name=f"Rolle {r}") for r in role_ids],
                           guild_permissions=SimpleNamespace(administrator=False))


MANAGER = person(111111111111111111, "Fraktionschef", TEAM_ROLE, FACTION_MANAGER_ROLE_ID)
PLAIN = person(222222222222222222, "Teamler", TEAM_ROLE)
LEADER = person(333333333333333333, "Don Bochum")
TWIN_A, TWIN_B = person(444444444444444444, "Max"), person(555555555555555555, "Max")
MEMBERS = [MANAGER, PLAIN, LEADER, TWIN_A, TWIN_B]
GUILD = SimpleNamespace(id=1, name="© BOCHUM RP | V1", members=MEMBERS, roles=[], channels=[], text_channels=[],
                        member_count=5, owner_id=0, icon=None, get_role=lambda _: None, get_channel=lambda _: None,
                        get_member=lambda uid: next((m for m in MEMBERS if m.id == uid), None))


def login_as(perms, member):
    """Like ws.auth: login + team check, then the route's own role check runs."""
    def fake_auth(request, session, perm="can_view_dashboard", admin=False):
        if not (perms.get("can_view_dashboard") or perms.get("is_admin")):
            raise HTTPException(status_code=403, detail="Kein Team")
        if admin and not perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="Nur Admins")
        if perm and not perms.get(perm):
            raise HTTPException(status_code=403, detail="Fehlt")
        user = {"id": str(getattr(member, "id", "9")), "global_name": getattr(member, "display_name", "Admin")}
        return SimpleNamespace(user=user, guild=GUILD, guild_name=GUILD.name, perms=perms,
                               config={"team_role_ids": [TEAM_ROLE]}, member=member, msg="", msg_ok=True)
    return fake_auth


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.audit_file = root / "audit_logs.json"
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(self.audit_file))]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def audit(self):
        return json.loads(self.audit_file.read_text(encoding="utf-8")) if self.audit_file.exists() else []

    def cat_id(self, name):
        return next(c["id"] for c in pf.list_categories() if c["name"] == name)


class DataModelTests(TempDb):
    def test_default_categories_are_seeded_once(self):
        self.assertEqual([c["name"] for c in pf.list_categories()], ["Staatlich", "Neutral", "Illegal"])
        for c in pf.list_categories():
            pf.delete_category(c["id"])
        self.assertEqual(pf.list_categories(), [])               # deleting all does not re-seed

    def test_faction_crud_and_validation(self):
        item = pf.create_faction({"name": "  Polizei   Bochum ", "status": "official", "category_id": self.cat_id("Staatlich")})
        self.assertEqual(item["name"], "Polizei Bochum")
        self.assertEqual((item["category_name"], item["max_warnings"], item["warnings"]), ("Staatlich", 3, 0))
        self.assertIsNotNone(item["official_since"])
        for bad in ({"name": "polizei bochum", "status": "official"},          # unique, case-insensitive
                    {"name": "X", "status": "official"},
                    {"name": "Gang", "status": "bogus"},
                    {"name": "Gang", "status": "official", "max_warnings": "0"},
                    {"name": "Gang", "status": "official", "category_id": "999"}):
            with self.assertRaises(ValueError, msg=bad):
                pf.create_faction(bad)
        before, after = pf.update_faction(item["id"], {"name": "LSPD", "status": "probation", "max_warnings": "5"})
        self.assertEqual((before["name"], after["name"], after["status"], after["max_warnings"]), ("Polizei Bochum", "LSPD", "probation", 5))
        self.assertIsNone(after["category_id"])
        self.assertIsNotNone(after["probation_since"])
        self.assertIsNone(after["official_since"])
        with self.assertRaises(LookupError):
            pf.update_faction(999, {"name": "Egal", "status": "official"})

    def test_make_official_only_from_probation(self):
        item = pf.create_faction({"name": "Mafia", "status": "probation"})
        self.assertEqual(pf.make_official(item["id"])["status"], "official")
        with self.assertRaises(ValueError):
            pf.make_official(item["id"])

    def test_warnings_counter_maximum_revoke_and_history(self):
        item = pf.create_faction({"name": "Bloods", "status": "official", "max_warnings": "2"})
        old = (date.today() - timedelta(days=3)).isoformat()
        w1, item = pf.add_warning(item["id"], "Massen-RDM", old, 1, "Lena")
        self.assertEqual((w1["warned_on"], item["warnings"]), (old, 1))
        with self.assertRaises(ValueError):
            pf.add_warning(item["id"], "Zukunft", (date.today() + timedelta(days=1)).isoformat(), 1, "Lena")
        with self.assertRaises(ValueError):
            pf.add_warning(item["id"], "x", "", 1, "Lena")             # reason too short
        _, item = pf.add_warning(item["id"], "Fail-RP", "", 1, "Lena")
        self.assertTrue(item["at_max"])
        with self.assertRaises(ValueError):                               # at the maximum: blocked, nothing automatic
            pf.add_warning(item["id"], "Noch eine", "", 1, "Lena")
        revoked, item = pf.revoke_warning(item["id"], w1["id"], "Admin", "Fehlentscheidung")
        self.assertEqual((revoked["revoke_reason"], item["warnings"], item["at_max"]), ("Fehlentscheidung", 1, False))
        self.assertEqual(len(pf.warnings_for(item["id"])), 2)          # revoked stays in the history
        with self.assertRaises(LookupError):
            pf.revoke_warning(item["id"], w1["id"], "Admin", "Doppelt")
        with self.assertRaises(LookupError):                              # warning of another faction
            other = pf.create_faction({"name": "Crips", "status": "official"})
            pf.revoke_warning(other["id"], w1["id"], "Admin", "Falsche Fraktion")

    def test_delete_category_keeps_factions_and_delete_faction_removes_warnings(self):
        cid = self.cat_id("Illegal")
        item = pf.create_faction({"name": "Vagos", "status": "official", "category_id": cid})
        pf.add_warning(item["id"], "Grund eins", "", 1, "Lena")
        cat, moved = pf.delete_category(cid)
        self.assertEqual((cat["name"], moved), ("Illegal", 1))
        self.assertIsNone(pf.get_faction(item["id"])["category_id"])
        deleted, removed = pf.delete_faction(item["id"])
        self.assertEqual((deleted["name"], removed), ("Vagos", 1))
        self.assertIsNone(pf.get_faction(item["id"]))
        self.assertEqual(pf.warnings_for(item["id"]), [])

    def test_summary_filters_and_search(self):
        pf.create_faction({"name": "LSPD", "status": "official", "category_id": self.cat_id("Staatlich")})
        pf.create_faction({"name": "Medic", "status": "official"})
        pf.create_faction({"name": "100%_Gang", "status": "probation", "leader_id": "1", "leader_name": "Don"})
        pf.create_faction({"name": "Frei", "status": "unassigned"})
        stats = pf.summary()
        self.assertEqual((stats["official"], stats["probation"], stats["unassigned"], stats["total"], stats["categories"]), (2, 1, 1, 4, 3))
        self.assertEqual([f["name"] for f in pf.list_factions("official")], ["LSPD", "Medic"])
        self.assertEqual([f["name"] for f in pf.list_factions(category_id=0)], ["100%_Gang", "Frei", "Medic"])
        self.assertEqual([f["name"] for f in pf.list_factions(q="%_")], ["100%_Gang"])   # LIKE wildcards escaped
        self.assertEqual([f["name"] for f in pf.list_factions(q="don")], ["100%_Gang"])  # leader name


class AccessTests(unittest.TestCase):
    def test_can_manage_is_role_or_admin_only(self):
        self.assertTrue(pf.can_manage({"is_admin": False}, MANAGER))
        self.assertTrue(pf.can_manage({"is_admin": True}, None))
        self.assertFalse(pf.can_manage({"is_admin": False}, PLAIN))
        self.assertFalse(pf.can_manage(dict(ADMIN, is_admin=False), PLAIN))  # other dashboard rights do not count
        self.assertFalse(pf.can_manage(None, None))

    def test_permission_model_exposes_menu_flag(self):
        config = {"team_role_ids": [TEAM_ROLE]}
        guild = SimpleNamespace(owner_id=1, get_member=lambda uid: next((m for m in MEMBERS if m.id == uid), None))
        self.assertTrue(pulse_permissions.compute(guild, MANAGER.id, config)[0]["can_manage_factions"])
        self.assertFalse(pulse_permissions.compute(guild, PLAIN.id, config)[0]["can_manage_factions"])
        self.assertFalse(pulse_permissions.compute(guild, 1234, config)[0]["can_manage_factions"])
        self.assertNotIn("can_manage_factions", pulse_permissions.ALL_KEYS)   # never configurable via team roles

    def test_sidebar_shows_entry_only_with_flag(self):
        self.assertIn('href="/factions"', ws.get_sidebar_html("G", "factions", {"id": "1"}, dict(TEAMLER, can_manage_factions=True)))
        self.assertNotIn('href="/factions"', ws.get_sidebar_html("G", "me", {"id": "1"}, TEAMLER))


class RouteTests(TempDb):
    WRITE_ROUTES = (
        ("/factions/create", {"name": "Neue", "status": "official"}),
        ("/factions/{fid}/update", {"name": "Umbenannt", "status": "unassigned"}),
        ("/factions/{fid}/official", {}),
        ("/factions/{fid}/delete", {"confirm": "1"}),
        ("/factions/{fid}/warnings/add", {"reason": "Regelbruch", "warned_on": ""}),
        ("/factions/{fid}/warnings/{wid}/revoke", {"reason": "Zurück"}),
        ("/factions/categories/create", {"name": "Neu", "color": "#123456"}),
        ("/factions/categories/{cid}/update", {"name": "Anders", "color": "#123456"}),
        ("/factions/categories/{cid}/delete", {}),
    )

    def setUp(self):
        super().setUp()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def post(self, url, data=None):
        return self.client.post(url, data=data or {}, follow_redirects=False)

    def state(self):
        with pulse_db.connect() as db:
            return [[tuple(r) for r in db.execute(f"SELECT * FROM {t} ORDER BY id").fetchall()]
                    for t in ("factions", "faction_categories", "faction_warnings")]

    def test_without_role_every_route_refuses_and_changes_nothing(self):
        item = pf.create_faction({"name": "Probe", "status": "probation", "category_id": self.cat_id("Neutral")})
        warning, _ = pf.add_warning(item["id"], "Vorhandene", "", 1, "Lena")
        before, audit_before = self.state(), self.audit()
        for perms, member in ((TEAMLER, PLAIN), (dict(ADMIN, is_admin=False), PLAIN), (TEAMLER, None),
                              (dict(TEAMLER, can_view_dashboard=False), MANAGER)):  # role but not in the team
            with patch.object(ws, "auth", login_as(perms, member)):
                self.assertEqual(self.client.get("/factions").status_code, 403)
                self.assertEqual(self.client.get(f"/factions/{item['id']}").status_code, 403)
                for url, data in self.WRITE_ROUTES:
                    url = url.format(fid=item["id"], wid=warning["id"], cid=self.cat_id("Neutral"))
                    self.assertEqual(self.post(url, data).status_code, 403, url)
        self.assertEqual(self.state(), before)
        self.assertEqual(self.audit(), audit_before)

    def test_without_login_nothing_is_shown_or_written(self):
        item = pf.create_faction({"name": "Probe", "status": "probation"})
        before = self.state()
        page = self.client.get("/factions", follow_redirects=False)
        self.assertNotEqual(page.status_code, 200)
        self.assertNotIn("Probe", page.text)
        for url, data in self.WRITE_ROUTES:
            url = url.format(fid=item["id"], wid=1, cid=1)
            self.assertIn(self.post(url, data).status_code, (303, 401, 403, 503), url)
        self.assertEqual(self.state(), before)

    def test_role_holder_full_crud_with_audit(self):
        with patch.object(ws, "auth", login_as(TEAMLER, MANAGER)):
            empty = self.client.get("/factions")
            self.assertEqual(empty.status_code, 200)
            self.assertIn("Noch keine Fraktionen angelegt", empty.text)

            r = self.post("/factions/create", {"name": "Grove Street", "status": "probation", "category_id": str(self.cat_id("Illegal")),
                                               "leader": "Don Bochum", "max_warnings": "3", "description": "Gang aus Wattenscheid"})
            self.assertIn("t=ok", r.headers["location"])
            fid = pf.list_factions()[0]["id"]
            self.assertEqual(pf.get_faction(fid)["leader_id"], str(LEADER.id))
            page = self.client.get("/factions")
            self.assertIn("Grove Street", page.text)
            self.assertIn("Offiziell machen", page.text)
            self.assertIn("Leiter: Don Bochum", page.text)
            detail = self.client.get(f"/factions/{fid}")
            self.assertEqual(detail.status_code, 200)
            self.assertIn("Keine Verwarnungen", detail.text)

            self.assertIn("t=err", self.post("/factions/create", {"name": "grove street", "status": "official"}).headers["location"])
            self.assertIn("t=err", self.post("/factions/create", {"name": "Zwilling", "status": "official", "leader": "Max"}).headers["location"])
            self.assertIn("t=err", self.post("/factions/create", {"name": "Geist", "status": "official", "leader": "999999999999999999"}).headers["location"])

            self.assertIn("t=ok", self.post(f"/factions/{fid}/official").headers["location"])
            self.assertEqual(pf.get_faction(fid)["status"], "official")
            self.assertIn("t=err", self.post(f"/factions/{fid}/official").headers["location"])

            r = self.post(f"/factions/{fid}/update", {"name": "Grove St.", "status": "official", "category_id": "",
                                                       "leader": str(LEADER.id), "max_warnings": "2", "description": ""})
            self.assertIn("t=ok", r.headers["location"])
            item = pf.get_faction(fid)
            self.assertEqual((item["name"], item["category_id"], item["max_warnings"]), ("Grove St.", None, 2))

            for reason in ("Fail-RP im Park", "RDM am Hauptbahnhof"):
                self.assertIn("t=ok", self.post(f"/factions/{fid}/warnings/add", {"reason": reason, "warned_on": ""}).headers["location"])
            self.assertIn("t=err", self.post(f"/factions/{fid}/warnings/add", {"reason": "Dritte", "warned_on": ""}).headers["location"])
            detail = self.client.get(f"/factions/{fid}")
            self.assertIn("Maximum erreicht", detail.text)
            wid = pf.warnings_for(fid)[0]["id"]
            self.assertIn("t=err", self.post(f"/factions/{fid}/warnings/{wid}/revoke", {"reason": ""}).headers["location"])
            self.assertIn("t=ok", self.post(f"/factions/{fid}/warnings/{wid}/revoke", {"reason": "Beweise fehlen"}).headers["location"])
            self.assertEqual(pf.get_faction(fid)["warnings"], 1)

            self.assertIn("t=ok", self.post("/factions/categories/create", {"name": "Wirtschaft", "color": "#00aa00"}).headers["location"])
            cid = self.cat_id("Wirtschaft")
            self.assertIn("t=err", self.post("/factions/categories/create", {"name": "wirtschaft"}).headers["location"])
            self.assertIn("t=err", self.post(f"/factions/categories/{cid}/update", {"name": "Wirtschaft", "color": "rot"}).headers["location"])
            self.assertIn("t=ok", self.post(f"/factions/categories/{cid}/update", {"name": "Firmen", "color": "#00aa00"}).headers["location"])
            self.assertIn("t=ok", self.post(f"/factions/categories/{cid}/delete").headers["location"])

            self.assertIn("t=err", self.post(f"/factions/{fid}/delete", {}).headers["location"])  # checkbox missing
            self.assertIsNotNone(pf.get_faction(fid))
            self.assertIn("t=ok", self.post(f"/factions/{fid}/delete", {"confirm": "1"}).headers["location"])
            self.assertIsNone(pf.get_faction(fid))
            self.assertIn("t=err", self.client.get(f"/factions/{fid}", follow_redirects=False).headers["location"])

        actions = [a["action"] for a in self.audit()]
        for expected in ("Fraktion angelegt", "Fraktion offiziell gemacht", "Fraktion bearbeitet", "Fraktion verwarnt",
                         "Fraktionsverwarnung zurückgezogen", "Fraktionskategorie angelegt", "Fraktionskategorie bearbeitet",
                         "Fraktionskategorie gelöscht", "Fraktion gelöscht"):
            self.assertIn(expected, actions)
        self.assertEqual(actions.count("Fraktion verwarnt"), 2)
        self.assertTrue(all(a["actor_id"] == str(MANAGER.id) for a in self.audit()))

    def test_admin_without_role_may_manage(self):
        with patch.object(ws, "auth", login_as(ADMIN, PLAIN)):
            self.assertEqual(self.client.get("/factions").status_code, 200)
            self.assertIn("t=ok", self.post("/factions/create", {"name": "Feuerwehr", "status": "official"}).headers["location"])
        self.assertEqual(pf.summary()["official"], 1)

    def test_filters_empty_filter_state_and_escaping(self):
        pf.create_faction({"name": "<b>Gang</b>", "status": "official"})
        with patch.object(ws, "auth", login_as(TEAMLER, MANAGER)):
            page = self.client.get("/factions")
            self.assertIn("&lt;b&gt;Gang&lt;/b&gt;", page.text)
            self.assertNotIn("<b>Gang</b>", page.text)
            none = self.client.get("/factions?status=probation&category=none&q=xyz")
            self.assertIn("Keine Fraktion passt zum Filter", none.text)
            self.assertEqual(self.client.get("/factions?status=evil&category=abc").status_code, 200)

    def test_database_error_shows_error_state(self):
        with patch.object(ws, "auth", login_as(TEAMLER, MANAGER)), \
                patch.object(pf, "summary", side_effect=sqlite3.OperationalError("database is locked")):
            page = self.client.get("/factions")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Daten nicht verfügbar", page.text)
        with patch.object(ws, "auth", login_as(TEAMLER, MANAGER)), \
                patch.object(pf, "create_faction", side_effect=sqlite3.OperationalError("database is locked")):
            r = self.post("/factions/create", {"name": "Egal", "status": "official"})
        self.assertIn("t=err", r.headers["location"])
        self.assertNotIn("Fraktion angelegt", [a["action"] for a in self.audit()])


if __name__ == "__main__":
    unittest.main()
