"""Audit #50: IC-/OOC-Modell (Validierung, Fälle, Spielermeldungen, Filter, Nachtragen, Registry; temp DB, ohne Discord)."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from fastapi.testclient import TestClient

import pulse_db
import pulse_player_reports as reports
import pulse_realm as realm
import pulse_case_registry as registry
import pulse_ultimate as pu
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

DESC = "Eine ausreichend lange Beschreibung des Vorfalls im Spiel."


class RealmUnitTests(unittest.TestCase):
    def test_normalize_label_matches_counts(self):
        self.assertEqual((realm.normalize(" IC "), realm.normalize("ooc"), realm.normalize("")), ("ic", "ooc", ""))
        for bad in ("beides", "ICC", "x"):
            with self.assertRaises(ValueError):
                realm.normalize(bad)
        with self.assertRaises(ValueError):
            realm.normalize("", allow_empty=False)
        self.assertEqual((realm.label("ic"), realm.label(""), realm.label(None)), (realm.REALMS["ic"], realm.UNASSIGNED_LABEL, realm.UNASSIGNED_LABEL))
        rows = [{"realm": "ic"}, {"realm": "ooc"}, {"realm": ""}, {}, {"realm": "kaputt"}, None]
        self.assertEqual(realm.counts(rows), {"ic": 1, "ooc": 1, "offen": 4})
        self.assertEqual([realm.matches("ic", f) for f in ("ic", "ooc", "offen", "")], [True, False, False, True])
        self.assertTrue(realm.matches("", "offen"))


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        config = Path(self.tmp.name) / "config.json"
        config.write_text("{}", encoding="utf-8")
        for p in (patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")), patch.object(pu, "_INIT", False),
                  patch.object(reports, "DB_PATH", str(Path(self.tmp.name) / "pulse_suite.db")), patch.object(ws, "CONFIG_FILE", str(config)),
                  patch.object(ws, "log_audit", lambda *a, **k: None), patch.object(ws, "auth", fake_auth_for(ADMIN)), patch("builtins.print")):
            p.start()
            self.addCleanup(p.stop)
        pulse_db.init_db()
        pu.setup()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def cases(self):
        return pu.case_rows(50)

    def create(self, **data):
        base = {"title": "Fall", "category": "moderation", "priority": "normal", "description": "x"}
        base.update(data)
        return self.client.post("/ultimate/cases/create", data=base, follow_redirects=False)


class CaseTests(Base):
    def test_case_creation_validates_the_realm(self):
        self.assertEqual(self.create(realm="ic").status_code, 303)
        self.assertEqual(self.create(realm="ooc", title="Zweiter").status_code, 303)
        self.assertEqual(self.create(realm="beides").status_code, 400)
        self.assertEqual(len(self.cases()), 2)
        self.assertEqual({c["realm"] for c in self.cases()}, {"ic", "ooc"})

    def test_legacy_caller_without_realm_still_works_and_is_unassigned(self):
        self.assertEqual(self.create().status_code, 303)
        [case] = self.cases()
        self.assertEqual(case["realm"], "")
        self.assertIn(realm.UNASSIGNED_LABEL, self.client.get("/ultimate/cases").text)

    def test_list_filters_strictly_and_shows_counts(self):
        self.create(realm="ic", title="Im Spiel")
        self.create(realm="ooc", title="Auf Discord")
        self.create(title="Offen")
        ic, ooc, offen = (self.client.get(f"/ultimate/cases?bereich={f}").text for f in ("ic", "ooc", "offen"))
        self.assertIn("Im Spiel", ic)
        self.assertNotIn("Auf Discord", ic)
        self.assertNotIn("Im Spiel", ooc)
        self.assertIn("Auf Discord", ooc)
        self.assertIn("Offen", offen)
        self.assertNotIn("Im Spiel", offen)
        everything = self.client.get("/ultimate/cases").text
        for needle in ("Alle (3)", "IC (1)", "OOC (1)", "Nicht zugeordnet (1)"):
            self.assertIn(needle, everything)

    def test_leadership_can_assign_later_and_registry_exposes_it(self):
        self.create(title="Altfall")
        [case] = self.cases()
        self.assertEqual(self.client.post(f"/ultimate/cases/{case['id']}/realm", data={"realm": "ooc"}, follow_redirects=False).status_code, 303)
        self.assertEqual(self.cases()[0]["realm"], "ooc")
        self.assertEqual(self.client.post(f"/ultimate/cases/{case['id']}/realm", data={"realm": ""}, follow_redirects=False).status_code, 422)
        self.assertEqual(self.client.post(f"/ultimate/cases/{case['id']}/realm", data={"realm": "x"}, follow_redirects=False).status_code, 400)
        self.assertEqual(self.client.post("/ultimate/cases/gibtesnicht/realm", data={"realm": "ic"}, follow_redirects=False).status_code, 404)
        entry = registry.resolve(case["case_no"], {"is_admin": True})
        self.assertEqual(entry["realm"], "ooc")

    def test_team_members_cannot_assign(self):
        self.create(title="Fall", realm="ic")
        [case] = self.cases()
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(self.client.post(f"/ultimate/cases/{case['id']}/realm", data={"realm": "ooc"}).status_code, 403)
        self.assertEqual(self.cases()[0]["realm"], "ic")

    def test_existing_database_without_the_column_is_migrated(self):
        import sqlite3
        with pu.cx() as c:
            c.execute("ALTER TABLE ultimate_cases DROP COLUMN realm")
        with patch.object(pu, "_INIT", False):
            pu.setup()
        with sqlite3.connect(pu.ultimate_db()) as c:
            self.assertIn("realm", {r[1] for r in c.execute("PRAGMA table_info(ultimate_cases)")})


class ReportTests(Base):
    def report(self, **kw):
        return reports.create_report(100, 900000000000000001, "Reporter", "Ziel Spieler", "rule_violation", DESC, "normal", None, **kw)

    def test_report_realm_is_validated_stored_and_optional(self):
        self.assertEqual(self.report(realm="ic")["realm"], "ic")
        self.assertEqual(self.report()["realm"], "")
        with self.assertRaises(ValueError):
            self.report(realm="beides")
        self.assertEqual(len(reports.list_reports(100)), 2)

    def test_set_realm_later_and_registry(self):
        row = self.report()
        self.assertTrue(reports.set_realm(row["id"], "ooc"))
        self.assertEqual(reports.get_report(row["id"])["realm"], "ooc")
        self.assertFalse(reports.set_realm("RPT-000000000000", "ic"))
        with self.assertRaises(ValueError):
            reports.set_realm(row["id"], "")
        entry = registry.resolve(row["id"], {"can_view_analytics": True}, guild_id=100)
        self.assertEqual(entry["realm"], "ooc")

    def test_old_report_table_without_the_column_is_migrated(self):
        import sqlite3
        row = self.report(realm="ic")
        with sqlite3.connect(reports.DB_PATH) as c:
            c.execute("ALTER TABLE discord_player_reports DROP COLUMN realm")
        self.assertEqual(reports.get_report(row["id"])["realm"], "")          # Migration beim nächsten Zugriff, Altwert nicht erfunden

    def test_discord_command_passes_the_selected_area(self):
        import cogs.pulse_suite as suite
        from cogs.pulse_suite import PulseSuite
        patcher = patch.object(suite, "DB_PATH", str(Path(self.tmp.name) / "suite.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        suite.init_db()
        guild = SimpleNamespace(id=100)
        inter = SimpleNamespace(guild=guild, user=SimpleNamespace(id=900000000000000001, display_name="Reporter"), response=SimpleNamespace(send_message=AsyncMock(), defer=AsyncMock()),
                                followup=SimpleNamespace(send=AsyncMock()))
        choice = discord.app_commands.Choice
        asyncio.run(PulseSuite.spieler_meldung.callback(None, inter, "Ziel Spieler", choice(name="Regelverstoß", value="rule_violation"), DESC,
                                                       choice(name="Normal", value="normal"), None, choice(name="IC", value="ic")))
        self.assertEqual([r["realm"] for r in reports.list_reports(100)], ["ic"])


if __name__ == "__main__":
    unittest.main()
