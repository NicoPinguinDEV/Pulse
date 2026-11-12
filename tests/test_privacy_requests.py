"""Audit #171: data-subject request workflow – deadlines, transitions, guild isolation, rights, backup, export, Discord."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_privacy_requests as dsr
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

GUILD = 111
OTHER = 222
UID = "100000000000000001"
UID2 = "100000000000000002"
NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.root = root
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(ws, "DATA_FILE", str(root / "team_data.json")),
                        patch.object(ws, "APPS_FILE", str(root / "applications.json")),
                        patch.object(ws, "SHIFTS_FILE", str(root / "shifts.json")),
                        patch.object(ws, "DATA_DIR", root),
                        patch.dict("os.environ", {"PULSE_AI_MODERATION_CASES_FILE": str(root / "ai.json")})]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def make(self, kind="access", uid=UID, guild=GUILD, details="", **kw):
        return dsr.create_request(guild, uid, "Spieler", kind, details, now=kw.pop("now", NOW), **kw)

    def notifications(self, uid):
        with pulse_db.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM notifications WHERE user_id=?", (str(uid),)).fetchall()]


class CreateTests(TempDb):
    def test_deadline_is_one_month_from_receipt(self):
        item = self.make(received_at=NOW - timedelta(days=3))
        self.assertTrue(item["id"].startswith("DSR-"))
        self.assertEqual(item["status"], "received")
        self.assertEqual(dsr._parse(item["due_at"]), NOW - timedelta(days=3) + timedelta(days=30))
        self.assertEqual(item["days_left"], 27)
        self.assertFalse(item["overdue"])
        self.assertEqual([e["action"] for e in dsr.history(GUILD, item["id"])], ["created"])

    def test_validation(self):
        for bad in ("abc", "123", "", None):
            with self.assertRaises(ValueError):
                self.make(uid=bad)
        with self.assertRaises(ValueError):
            self.make(kind="delete_everything")
        with self.assertRaises(ValueError):
            self.make(kind="rectification", details="kurz")            # needs a description
        with self.assertRaises(ValueError):
            self.make(details="x" * (dsr.MAX_DETAILS + 1))
        with self.assertRaises(ValueError):
            self.make(channel="fax")
        with self.assertRaises(ValueError):
            self.make(received_at=NOW + timedelta(days=2))
        with self.assertRaises(ValueError):
            self.make(received_at=NOW - timedelta(days=400))
        with self.assertRaises(ValueError):
            dsr.create_request(None, UID, "x", "access", now=NOW)
        self.assertEqual(dsr.list_requests(GUILD, state="all"), [])        # nothing half-written

    def test_duplicates_and_open_limit(self):
        first = self.make("access")
        with self.assertRaises(ValueError) as ctx:
            self.make("access")
        self.assertIn(first["id"], str(ctx.exception))
        self.make("erasure")
        self.make("restriction")
        with self.assertRaises(ValueError):
            self.make("objection", details="Ich widerspreche der Verarbeitung.")
        self.make("access", guild=OTHER)                                     # other guild is independent
        dsr.set_status(GUILD, first["id"], "completed", "9", "Admin", "Export per DM übermittelt.", now=NOW)
        self.make("access")                                                  # closed request no longer blocks

    def test_guild_isolation(self):
        item = self.make()
        self.assertIsNone(dsr.get_request(OTHER, item["id"]))
        self.assertEqual(dsr.list_requests(OTHER, state="all"), [])
        with self.assertRaises(LookupError):
            dsr.set_status(OTHER, item["id"], "in_progress", "9", "Admin")
        with self.assertRaises(LookupError):
            dsr.add_note(OTHER, item["id"], "fremd", "9", "Admin")
        self.assertEqual(dsr.history(OTHER, item["id"]), [])


class WorkflowTests(TempDb):
    def test_status_transitions_and_history(self):
        item = self.make("erasure")
        dsr.set_status(GUILD, item["id"], "in_progress", "9", "Admin", now=NOW)
        with self.assertRaises(ValueError):
            dsr.set_status(GUILD, item["id"], "in_progress", "9", "Admin", now=NOW)   # unchanged
        with self.assertRaises(ValueError):
            dsr.set_status(GUILD, item["id"], "completed", "9", "Admin", "ok", now=NOW)  # result too short
        with self.assertRaises(ValueError):
            dsr.set_status(GUILD, item["id"], "deleted", "9", "Admin", now=NOW)
        done = dsr.set_status(GUILD, item["id"], "completed", "9", "Admin", "Bewerbung und Teamakte gelöscht.", now=NOW)
        self.assertFalse(done["is_open"])
        self.assertEqual(done["closed_by_id"], "9")
        self.assertEqual(done["resolution"], "Bewerbung und Teamakte gelöscht.")
        with self.assertRaises(ValueError):
            dsr.set_status(GUILD, item["id"], "rejected", "9", "Admin", "Doch abgelehnt, weil …", now=NOW)
        reopened = dsr.set_status(GUILD, item["id"], "in_progress", "9", "Admin", "Nachfrage der Person", now=NOW)
        self.assertTrue(reopened["is_open"])
        self.assertIsNone(reopened["closed_at"])
        actions = [e["action"] for e in dsr.history(GUILD, item["id"])]
        self.assertEqual(actions, ["created", "status", "status", "reopened"])

    def test_extension_once_with_reason(self):
        item = self.make()
        with self.assertRaises(ValueError):
            dsr.extend(GUILD, item["id"], 30, "kurz", "9", "Admin", now=NOW)
        for days in (0, 61, "x"):
            with self.assertRaises(ValueError):
                dsr.extend(GUILD, item["id"], days, "Sehr viele Anfragen gleichzeitig", "9", "Admin", now=NOW)
        extended = dsr.extend(GUILD, item["id"], 30, "Sehr viele Anfragen gleichzeitig", "9", "Admin", now=NOW)
        self.assertEqual(dsr._parse(extended["due_at"]), NOW + timedelta(days=60))
        with self.assertRaises(ValueError):
            dsr.extend(GUILD, item["id"], 10, "Noch einmal verlängern bitte", "9", "Admin", now=NOW)

    def test_assign_and_notes(self):
        item = self.make()
        self.assertEqual(dsr.assign(GUILD, item["id"], UID2, "Admin B", "9", "Admin")["assignee_name"], "Admin B")
        self.assertIsNone(dsr.assign(GUILD, item["id"], None, "", "9", "Admin")["assignee_id"])
        with self.assertRaises(ValueError):
            dsr.assign(GUILD, item["id"], "keine-id", "x", "9", "Admin")
        with self.assertRaises(ValueError):
            dsr.add_note(GUILD, item["id"], "", "9", "Admin")
        dsr.add_note(GUILD, item["id"], "Identität per Ticket bestätigt", "9", "Admin")
        dsr.set_status(GUILD, item["id"], "completed", "9", "Admin", "Auskunft als JSON übermittelt.")
        with self.assertRaises(ValueError):
            dsr.assign(GUILD, item["id"], UID2, "Admin B", "9", "Admin")

    def test_overdue_and_reminders(self):
        old = self.make("access", received_at=NOW - timedelta(days=40), now=NOW)
        fresh = self.make("erasure", received_at=NOW - timedelta(days=25), now=NOW)
        self.assertEqual([r["id"] for r in dsr.list_requests(GUILD, state="overdue", now=NOW)], [old["id"]])
        self.assertEqual(dsr.summary(GUILD, now=NOW), {"open": 2, "overdue": 1, "due_soon": 1})
        self.assertEqual(dsr.overdue_reminders(GUILD, ["555555555555555555"], now=NOW), 1)   # unassigned → admins
        dsr.assign(GUILD, old["id"], UID2, "Admin B", "9", "Admin")
        self.assertEqual(dsr.overdue_reminders(GUILD, ["555555555555555555"], now=NOW), 1)   # assigned → assignee only
        dsr.overdue_reminders(GUILD, [], now=NOW)                                            # same day: deduplicated
        self.assertEqual(len(self.notifications(UID2)), 1)
        self.assertEqual(len(self.notifications("555555555555555555")), 1)
        dsr.set_status(GUILD, old["id"], "completed", "9", "Admin", "Auskunft verspätet übermittelt.", now=NOW)
        self.assertEqual(dsr.list_requests(GUILD, state="overdue", now=NOW), [])
        self.assertNotIn(fresh["id"], [r["id"] for r in dsr.list_requests(GUILD, state="closed")])


class DataTests(TempDb):
    def test_json_backup_roundtrip(self):
        item = self.make("erasure")
        dsr.add_note(GUILD, item["id"], "Prüfung läuft", "9", "Admin")
        state = pulse_db.export_state()
        self.assertEqual(len(state["privacy_requests"]), 1)
        self.assertEqual(len(state["privacy_request_events"]), 2)
        with pulse_db.connect() as db:
            db.execute("DELETE FROM privacy_requests")
            db.execute("DELETE FROM privacy_request_events")
        pulse_db.import_state(state)
        self.assertEqual(dsr.get_request(GUILD, item["id"])["kind"], "erasure")
        self.assertEqual(len(dsr.history(GUILD, item["id"])), 2)

    def test_personal_export_contains_own_requests_and_their_history_only(self):
        import pulse_retention
        mine = self.make("access")
        dsr.add_note(GUILD, mine["id"], "Notiz zu meiner Anfrage", "9", "Admin")
        other = self.make("access", uid=UID2)
        data = pulse_retention.export_person(ws, UID)["databases"]["pulse.db"]
        self.assertEqual([r["id"] for r in data["privacy_requests"]], [mine["id"]])
        self.assertEqual({r["request_id"] for r in data["privacy_request_events"]}, {mine["id"]})
        self.assertNotIn(other["id"], str(data))

    def test_data_overview_counts_without_changing(self):
        export = {"team_file": {"rank": "x"}, "applications": {"a": {}, "b": {}}, "shifts": {"history": [1, 2], "active": None},
                  "databases": {"pulse.db": {"tickets": [1, 2, 3]}}, "audit_as_actor": []}
        self.assertEqual(dsr.data_overview(export), [("Teamakte", 1), ("Bewerbungen", 2), ("Schichten", 2), ("pulse.db · tickets", 3)])
        self.assertEqual(dsr.data_overview({}), [])


class PageTests(TempDb):
    def client(self):
        return TestClient(ws.app, raise_server_exceptions=False)

    def test_non_admin_is_refused_everywhere_without_writes(self):
        item = self.make()
        client = self.client()
        with patch.object(ws, "GUILD_ID", GUILD), patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            for path in ("/admin/privacy/requests", f"/admin/privacy/requests/{item['id']}"):
                self.assertEqual(client.get(path).status_code, 403, path)
            posts = {"/admin/privacy/requests/create": {"user_id": UID2, "kind": "access", "channel": "ticket"},
                     f"/admin/privacy/requests/{item['id']}/status": {"status": "completed", "note": "Erledigt durch Teamler"},
                     f"/admin/privacy/requests/{item['id']}/assign": {"assignee_id": "123"},
                     f"/admin/privacy/requests/{item['id']}/extend": {"days": "10", "reason": "Teamler verlängert"},
                     f"/admin/privacy/requests/{item['id']}/note": {"note": "Teamler-Notiz"}}
            for path, data in posts.items():
                self.assertEqual(client.post(path, data=data, follow_redirects=False).status_code, 403, path)
        self.assertEqual(len(dsr.list_requests(GUILD, state="all")), 1)
        self.assertEqual(dsr.get_request(GUILD, item["id"])["status"], "received")
        self.assertEqual(len(dsr.history(GUILD, item["id"])), 1)

    def test_admin_workflow(self):
        client = self.client()
        with patch.object(ws, "GUILD_ID", GUILD), patch.object(ws, "auth", fake_auth_for(ADMIN)):
            res = client.post("/admin/privacy/requests/create", follow_redirects=False,
                              data={"user_id": UID, "user_name": "Max", "kind": "erasure", "channel": "ticket",
                                    "received_on": "2026-11-01", "details": "Bitte alles löschen"})
            self.assertEqual(res.status_code, 303)
            item = dsr.list_requests(GUILD, state="all")[0]
            self.assertIn(item["id"], res.headers["location"])
            self.assertTrue(item["received_at"].startswith("2026-11-01"))
            page = client.get("/admin/privacy/requests")
            self.assertEqual(page.status_code, 200)
            self.assertIn(item["id"], page.text)
            detail = client.get(f"/admin/privacy/requests/{item['id']}")
            self.assertIn("Bitte alles löschen", detail.text)
            self.assertIn("Datenbestand prüfen", detail.text)
            with patch("pulse_ip_audit.entries_for_user", return_value=[]):
                scan = client.get(f"/admin/privacy/requests/{item['id']}?scan=1")
            self.assertIn("pulse.db · privacy_requests", scan.text)
            bad = client.post(f"/admin/privacy/requests/{item['id']}/status", data={"status": "completed", "note": "kurz"},
                              follow_redirects=False)
            self.assertIn("t=err", bad.headers["location"])
            client.post(f"/admin/privacy/requests/{item['id']}/assign", data={"assignee_id": "123"}, follow_redirects=False)
            refused = client.post(f"/admin/privacy/requests/{item['id']}/assign", data={"assignee_id": UID2}, follow_redirects=False)
            self.assertIn("t=err", refused.headers["location"])                 # only administrators may be assigned
            client.post(f"/admin/privacy/requests/{item['id']}/status",
                        data={"status": "completed", "note": "Bewerbung und Teamakte von Hand gelöscht."}, follow_redirects=False)
            done = dsr.get_request(GUILD, item["id"])
            self.assertEqual((done["status"], done["assignee_id"]), ("completed", "123"))
            missing = client.get("/admin/privacy/requests/DSR-000000", follow_redirects=False)
            self.assertEqual(missing.status_code, 303)
            privacy = client.get("/admin/privacy")
            self.assertIn("Datenschutzanfragen", privacy.text)

    def test_foreign_guild_request_is_not_reachable(self):
        foreign = self.make(guild=OTHER)
        client = self.client()
        with patch.object(ws, "GUILD_ID", GUILD), patch.object(ws, "auth", fake_auth_for(ADMIN)):
            res = client.get(f"/admin/privacy/requests/{foreign['id']}", follow_redirects=False)
            self.assertEqual(res.status_code, 303)
            client.post(f"/admin/privacy/requests/{foreign['id']}/note", data={"note": "fremder Server"}, follow_redirects=False)
        self.assertEqual(len(dsr.history(OTHER, foreign["id"])), 1)


class FakeResponse:
    def __init__(self):
        self.sent = []

    async def send_message(self, content=None, **kwargs):
        self.sent.append((content, kwargs))


def interaction(uid=UID, guild=True, locale="de"):
    owner = SimpleNamespace(id=999999999999999999, bot=False, display_name="Owner",
                            guild_permissions=SimpleNamespace(administrator=True))
    g = SimpleNamespace(id=GUILD, owner_id=owner.id, members=[owner]) if guild else None
    return SimpleNamespace(user=SimpleNamespace(id=int(uid), display_name="Max"), guild=g, locale=locale, response=FakeResponse(),
                           followup=SimpleNamespace(send=AsyncMock()))


class DiscordTests(TempDb):
    def test_player_files_own_request_privately_and_admins_are_notified(self):
        from cogs import privacy_requests as cog
        inter = interaction()
        with patch("pulse_i18n.get_locale", return_value="de"):
            asyncio.run(cog.submit(inter, "access", ""))
        content, kwargs = inter.response.sent[0]
        self.assertTrue(kwargs.get("ephemeral"))
        rows = dsr.list_requests(GUILD, state="all")
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["user_id"], rows[0]["channel"], rows[0]["created_by_id"]), (UID, "discord", UID))
        self.assertIn(rows[0]["id"], content)
        self.assertEqual(len(self.notifications("999999999999999999")), 1)
        again = interaction()
        with patch("pulse_i18n.get_locale", return_value="de"):
            asyncio.run(cog.submit(again, "access", ""))
        self.assertIn("bereits eine offene Anfrage", again.response.sent[0][0])
        self.assertEqual(len(dsr.list_requests(GUILD, state="all")), 1)

    def test_status_shows_only_own_requests_and_english_locale(self):
        from cogs import privacy_requests as cog
        mine = self.make("access")
        other = self.make("erasure", uid=UID2)
        inter = interaction(locale="en")
        with patch("pulse_i18n.get_locale", return_value="en"):
            asyncio.run(cog.show_status(inter))
        content, kwargs = inter.response.sent[0]
        self.assertTrue(kwargs.get("ephemeral"))
        self.assertIn(mine["id"], content)
        self.assertNotIn(other["id"], content)
        self.assertIn("Your privacy requests", content)

    def test_guild_only_and_storage_failure(self):
        import sqlite3

        from cogs import privacy_requests as cog
        inter = interaction(guild=False)
        with patch("pulse_i18n.get_locale", return_value="de"):
            asyncio.run(cog.submit(inter, "access", ""))
        self.assertIn("nur auf dem Server", inter.response.sent[0][0])
        broken = interaction()
        with patch("pulse_i18n.get_locale", return_value="de"), \
                patch.object(dsr, "create_request", side_effect=sqlite3.OperationalError("locked")):
            asyncio.run(cog.submit(broken, "access", ""))
        self.assertIn("nicht gespeichert", broken.response.sent[0][0])

    def test_no_automatic_data_change(self):
        source = Path(dsr.__file__).read_text(encoding="utf-8") + (Path(dsr.__file__).parent / "cogs" / "privacy_requests.py").read_text(encoding="utf-8")
        for forbidden in ("DELETE FROM", "purge", ".ban(", ".kick("):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
