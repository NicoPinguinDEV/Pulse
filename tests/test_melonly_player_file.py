"""Melonly: personal player file (Spielerakte) filtered by the Roblox player id.

Real FastAPI routes with a fake login, a temp JSON log file and no network (no Roblox, no Discord).
"""
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_melonly as pm
import webserver as ws
from pulse_melonly_repository import JsonMelonlyRepository
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

ALICE, BOB = "1001", "2002"


def entry(log_id, name, rid, kind, reason, when, moderator="Mod Eins"):
    return {"id": log_id, "target_user": name, "roblox_id": rid, "type": kind, "reason": reason,
            "moderator": moderator, "moderator_id": "9", "created_at": when}


ROWS = [
    entry("a1", "AliceRB", ALICE, "Warn", "alice warn grund", "01.03.2026 10:00"),
    entry("b1", "BobRB", BOB, "Ban", "bob ban grund", "02.03.2026 10:00"),
    entry("a2", "AliceRB", ALICE, "Ban", "alice ban grund", "05.03.2026 12:30"),
    entry("a3", "AliceRB", ALICE, "Ban BOLO", "alice bolo grund", "04.03.2026 09:15"),
    entry("a4", "AliceRB", ALICE, "Kick", "alice kick grund", "03.03.2026 08:00"),
    entry("a5", "AliceRB", ALICE, "Notiz", "alice notiz grund", "06.03.2026 18:45"),
    entry("b2", "BobRB", BOB, "Notiz", "bob notiz grund", "07.03.2026 10:00"),
    entry("x1", "AliceRB", "7777", "Kick", "gleicher name andere id", "08.03.2026 10:00"),
    entry("l1", "AliceRB", "N/A", "Warn", "legacy ohne id alice", "09.03.2026 10:00"),
    entry("l2", "FremdRB", "N/A", "Warn", "legacy ohne id fremd", "09.03.2026 11:00"),
]
WARN_ONLY = dict({k: False for k in ws.PERM_KEYS}, can_view_dashboard=True, can_warn=True)
NO_MELONLY = dict({k: False for k in ws.PERM_KEYS}, can_view_dashboard=True)


class PlayerFileBase(unittest.TestCase):
    perms = ADMIN

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "logs.json"
        self.path.write_text(json.dumps(ROWS, ensure_ascii=False), encoding="utf-8")
        self.repo = JsonMelonlyRepository(self.path, ws.load_json, ws.save_json)
        self.patches = [
            patch.object(ws, "get_melonly_repository", return_value=self.repo),
            patch.object(ws, "auth", fake_auth_for(self.perms)),
            patch.object(ws, "log_audit", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers={"Origin": "http://testserver"})
        self.before = self.path.read_text(encoding="utf-8")

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def file(self, rid, username="", **params):
        return self.client.get("/api/melonly/player-file", params={"roblox_id": rid, "username": username, **params})


class PureFilterTests(unittest.TestCase):
    def test_id_is_authoritative_and_legacy_entries_match_by_exact_name_only(self):
        file = pm.build_player_file(ROWS, ALICE, "AliceRB")
        self.assertEqual({e["id"] for e in file["entries"]}, {"a1", "a2", "a3", "a4", "a5", "l1"})
        self.assertEqual(file["name_matched"], 1)
        self.assertNotIn("x1", {e["id"] for e in file["entries"]}, "gleicher Name, aber andere Player ID")
        self.assertNotIn("l2", {e["id"] for e in file["entries"]})

    def test_without_a_name_legacy_entries_are_not_guessed(self):
        file = pm.build_player_file(ROWS, ALICE, "")
        self.assertEqual({e["id"] for e in file["entries"]}, {"a1", "a2", "a3", "a4", "a5"})

    def test_counts_and_chronological_order(self):
        file = pm.build_player_file(ROWS, ALICE, "AliceRB")
        self.assertEqual(file["counts"], {"bans": 2, "kicks": 1, "warns": 2, "notes": 1, "total": 6})
        self.assertEqual([e["id"] for e in file["entries"]], ["l1", "a5", "a2", "a3", "a4", "a1"], "neueste zuerst")
        self.assertEqual((file["first_at"], file["last_at"]), ("01.03.2026 10:00", "09.03.2026 10:00"))

    def test_invalid_ids_and_malformed_rows_never_match(self):
        for bad in ("", "N/A", "abc", "12 34", "1" * 16, None):
            self.assertEqual(pm.build_player_file(ROWS, bad, "AliceRB")["entries"], [])
        rows = ["kaputt", None, 5, {"roblox_id": ALICE}, {"roblox_id": 1001, "type": "Warn"}]
        self.assertEqual(pm.build_player_file(rows, ALICE, "x")["counts"]["total"], 2, "numerische IDs aus Altdaten")

    def test_other_names_of_the_same_id_are_listed(self):
        rows = [entry("1", "AlterName", ALICE, "Warn", "r", "01.01.2026 10:00"), entry("2", "NeuerName", ALICE, "Kick", "r", "02.01.2026 10:00")]
        self.assertEqual(pm.build_player_file(rows, ALICE, "NeuerName")["other_names"], ["AlterName"])

    def test_undated_entries_keep_store_order_at_the_end(self):
        rows = [entry("1", "A", ALICE, "Warn", "r", ""), entry("2", "A", ALICE, "Warn", "r", "01.01.2026 10:00"), entry("3", "A", ALICE, "Warn", "r", "kaputt")]
        self.assertEqual([e["id"] for e in pm.build_player_file(rows, ALICE, "A")["entries"]], ["2", "3", "1"])


class PlayerFileRouteTests(PlayerFileBase):
    def test_without_a_search_the_global_list_of_all_players_is_shown(self):
        page = self.client.get("/melonly")
        self.assertEqual(page.status_code, 200)
        for reason in ("alice warn grund", "bob ban grund", "legacy ohne id fremd"):
            self.assertIn(reason, page.text)
        self.assertNotIn('id="melonlyFileCard"', page.text)
        self.assertRegex(page.text, r'<div id="melonlyFile" class="hidden"')
        self.assertNotRegex(page.text, r'<div id="melonlyGlobal" class="hidden"')

    def test_file_contains_only_the_searched_player(self):
        data = self.file(ALICE, "AliceRB").json()
        self.assertTrue(data["success"])
        self.assertEqual(data["roblox_id"], ALICE)
        for reason in ("alice warn grund", "alice ban grund", "alice bolo grund", "alice kick grund", "alice notiz grund", "legacy ohne id alice"):
            self.assertIn(reason, data["html"])
        for foreign in ("bob ban grund", "bob notiz grund", "gleicher name andere id", "legacy ohne id fremd"):
            self.assertNotIn(foreign, data["html"])
        self.assertIn("Spielerakte – AliceRB", data["html"])
        self.assertEqual(data["counts"], {"bans": 2, "kicks": 1, "warns": 2, "notes": 1, "total": 6})

    def test_second_player_is_a_different_file_without_stale_data(self):
        alice = self.file(ALICE, "AliceRB").json()["html"]
        bob = self.file(BOB, "BobRB").json()
        self.assertIn("bob ban grund", bob["html"])
        self.assertNotIn("alice", bob["html"].lower())
        self.assertEqual(bob["counts"]["total"], 2)
        self.assertNotIn("bob ban grund", alice)

    def test_history_is_chronological_newest_first(self):
        html = self.file(ALICE, "AliceRB").json()["html"]
        order = [html.index(text) for text in ("legacy ohne id alice", "alice notiz grund", "alice ban grund", "alice bolo grund", "alice kick grund", "alice warn grund")]
        self.assertEqual(order, sorted(order))

    def test_entries_show_type_reason_time_moderator_and_id(self):
        html = self.file(ALICE, "AliceRB").json()["html"]
        for text in ("Ban BOLO", "alice bolo grund", "04.03.2026 09:15", "von Mod Eins", "Roblox ID: 1001"):
            self.assertIn(text, html)

    def test_unknown_player_gets_a_clean_empty_state_and_no_foreign_entries(self):
        data = self.file("424242", "NeuerSpieler").json()
        self.assertTrue(data["success"])
        self.assertIn("Für diesen Spieler liegen noch keine Vorgänge vor.", data["html"])
        self.assertEqual(data["counts"]["total"], 0)
        for text in ("alice", "bob", "FremdRB", "grund"):
            self.assertNotIn(text, data["html"])
        self.assertIn("NeuerSpieler", data["html"])

    def test_invalid_ids_are_rejected_without_data(self):
        for bad in ("", "abc", "N/A", "12 34", "1" * 16, "-5", "1;2"):
            response = self.file(bad, "AliceRB")
            self.assertEqual(response.status_code, 400, bad)
            self.assertFalse(response.json()["success"])
            self.assertNotIn("alice", response.text)

    def test_username_with_markup_is_escaped(self):
        data = self.file("424242", '<img src=x onerror=alert(1)>').json()
        self.assertNotIn("<img src=x", data["html"])
        self.assertIn("&lt;img", data["html"])

    def test_lookups_never_change_the_stored_data(self):
        self.file(ALICE, "AliceRB")
        self.file("424242", "Neu")
        self.client.get("/melonly")
        self.client.get(f"/melonly?player_file={ALICE}")
        self.assertEqual(self.path.read_text(encoding="utf-8"), self.before, "Suchen/Eingabe dürfen keinen Vorgang anlegen")

    def test_page_opened_with_a_player_file_hides_everyone_else(self):
        page = self.client.get(f"/melonly?player_file={ALICE}")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="melonlyFileCard"', page.text)
        self.assertIn("Spielerakte – AliceRB", page.text)
        self.assertIn("alice ban grund", page.text)
        for foreign in ("bob ban grund", "bob notiz grund", "legacy ohne id fremd", "gleicher name andere id"):
            self.assertNotIn(foreign, page.text, "die globale Liste wird in diesem Zustand gar nicht erst ausgeliefert")
        self.assertRegex(page.text, r'<div id="melonlyGlobal" class="hidden" data-lazy="1"')

    def test_page_with_an_invalid_player_file_falls_back_to_the_global_list(self):
        page = self.client.get("/melonly?player_file=abc")
        self.assertIn("bob ban grund", page.text)
        self.assertNotIn('id="melonlyFileCard"', page.text)

    def test_page_script_and_form_hooks(self):
        page = self.client.get("/melonly").text
        for hook in ('id="melonlyReturnTo"', "/api/melonly/player-file", "fileToken", "token!==fileToken", "Spielerakte wird geladen"):
            self.assertIn(hook, page)
        self.assertIn('name="return_to" value="/melonly"', page)

    def test_inline_script_is_valid_javascript(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node nicht installiert")
        page = self.client.get("/melonly").text
        scripts = [s for s in re.findall(r"<script>(.*?)</script>", page, re.S) if "melonlyFile" in s]
        self.assertEqual(len(scripts), 1)
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "melonly.js"
            target.write_text(scripts[0], encoding="utf-8")
            result = subprocess.run([node, "--check", str(target)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rate_limit_is_answered_with_429(self):
        with patch.object(ws, "rate_limited", return_value=True):
            response = self.file(ALICE, "AliceRB")
        self.assertEqual(response.status_code, 429)
        self.assertNotIn("alice", response.text)


class PlayerFileWritesTests(PlayerFileBase):
    def post(self, path, **data):
        return self.client.post(path, data=data, follow_redirects=False)

    def stored(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def test_saving_returns_to_the_players_file_and_the_entry_shows_up_at_once(self):
        response = self.post("/log/create", target_user="AliceRB", roblox_id=ALICE, log_type="Warn", reason="neuer vorgang zwei",
                             return_to=f"/melonly?player_file={ALICE}")
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith(f"/melonly?player_file={ALICE}&msg="))
        self.assertEqual(len(self.stored()), len(ROWS) + 1)
        page = self.client.get(f"/melonly?player_file={ALICE}").text
        self.assertIn("neuer vorgang zwei", page)
        self.assertNotIn("bob ban grund", page)

    def test_new_entry_is_assigned_to_the_entered_player_id(self):
        self.post("/log/create", target_user="Brandneu", roblox_id="555", log_type="Notiz", reason="erste notiz", return_to="/melonly?player_file=555")
        new = self.stored()[-1]
        self.assertEqual((new["target_user"], new["roblox_id"], new["type"]), ("Brandneu", "555", "Notiz"))
        data = self.file("555", "Brandneu").json()
        self.assertEqual(data["counts"], {"bans": 0, "kicks": 0, "warns": 0, "notes": 1, "total": 1})

    def test_edit_and_delete_keep_working_and_return_to_the_file(self):
        edited = self.post("/log/edit", log_id="a1", target_user="AliceRB", roblox_id=ALICE, log_type="Warn", reason="geaendert",
                           return_to=f"/melonly?player_file={ALICE}")
        self.assertTrue(edited.headers["location"].startswith(f"/melonly?player_file={ALICE}&msg="))
        self.assertEqual(next(r for r in self.stored() if r["id"] == "a1")["reason"], "geaendert")
        deleted = self.post("/log/delete", log_id="a2", return_to=f"/melonly?player_file={ALICE}")
        self.assertTrue(deleted.headers["location"].startswith(f"/melonly?player_file={ALICE}&msg="))
        self.assertNotIn("a2", {r["id"] for r in self.stored()})
        self.assertEqual(self.file(ALICE, "AliceRB").json()["counts"]["bans"], 1)

    def test_return_to_stays_a_local_whitelisted_path(self):
        for bad in ("/melonly?player_file=abc", "/melonly?player_file=1&x=<script>", "//evil.example", "https://evil.example",
                    "/melonly?player_file=" + "1" * 16, "/melonly?player_file=12%0d%0a"):
            self.assertEqual(ws.safe_log_return_to(bad), "/dashboard", bad)
        self.assertEqual(ws.safe_log_return_to("/melonly?player_file=1001"), "/melonly?player_file=1001")
        self.assertEqual(ws.safe_log_return_to("/melonly"), "/melonly")
        self.assertEqual(ws.safe_log_return_to("/melonly?player_history=Alice"), "/melonly?player_history=Alice")


class PlayerFilePermissionTests(unittest.TestCase):
    def run_as(self, perms):
        base = PlayerFileBase()
        base.perms = perms
        base.setUp()
        self.addCleanup(base.tearDown)
        return base

    def test_users_without_melonly_rights_get_nothing(self):
        base = self.run_as(NO_MELONLY)
        for url in (f"/api/melonly/player-file?roblox_id={ALICE}&username=AliceRB", "/melonly", f"/melonly?player_file={ALICE}"):
            response = base.client.get(url)
            self.assertEqual(response.status_code, 403, url)
            self.assertNotIn("alice", response.text)

    def test_team_members_without_melonly_rights_are_refused_too(self):
        base = self.run_as(TEAMLER)
        self.assertEqual(base.client.get(f"/api/melonly/player-file?roblox_id={ALICE}").status_code, 403)

    def test_edit_controls_follow_the_per_type_permission(self):
        base = self.run_as(WARN_ONLY)
        html = base.client.get(f"/api/melonly/player-file?roblox_id={ALICE}&username=AliceRB").json()["html"]
        warn = re.search(r'<article id="melonly-entry-a1".*?</article>', html, re.S).group(0)
        ban = re.search(r'<article id="melonly-entry-a2".*?</article>', html, re.S).group(0)
        self.assertIn("Bearbeiten", warn)
        self.assertNotIn("Bearbeiten", ban)
        self.assertNotIn("Löschen", ban)

    def test_manipulated_ids_cannot_reach_other_players_through_the_file(self):
        base = self.run_as(WARN_ONLY)
        for rid in ("1001 OR 1=1", "1001,2002", "*", "../2002"):
            response = base.client.get("/api/melonly/player-file", params={"roblox_id": rid, "username": "BobRB"})
            self.assertEqual(response.status_code, 400)
            self.assertNotIn("bob", response.text)

    def test_unauthorised_writes_are_still_refused(self):
        base = self.run_as(NO_MELONLY)
        response = base.client.post("/log/create", data={"target_user": "X", "roblox_id": "1", "log_type": "Warn", "reason": "r",
                                                         "return_to": "/melonly?player_file=1"}, follow_redirects=False)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(base.path.read_text(encoding="utf-8"), base.before)


if __name__ == "__main__":
    unittest.main()
