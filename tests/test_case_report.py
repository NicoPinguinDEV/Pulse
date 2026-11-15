"""Audit #175: closing report and internal notes for team cases (temp database, fake login, no Discord)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_case_report as report
import pulse_db
import pulse_ultimate
import webserver as ws

LEAD = {"can_view_dashboard": True, "can_promote": True}
TEAM = {"can_view_dashboard": True}
LONG = "Spieler wurde im Gespräch angehört und der Vorfall geklärt."


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.db = str(root / "pulse.db")
        self.patches = [patch.object(pulse_db, "DB_PATH", self.db), patch.object(pulse_ultimate, "_INIT", False),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit.json"))]
        for p in self.patches:
            p.start()
        pulse_db._SCHEMA_READY.discard(self.db)
        pulse_db.init_db()
        pulse_ultimate.setup()
        with pulse_ultimate.cx() as c:
            c.execute("INSERT INTO ultimate_cases(id,case_no,title,category,priority,status,description,created_by_id,created_by_name,created_at,updated_at) "
                      "VALUES('c1','PX-2026-1','Test','general','normal','open','Beschreibung','9','Lead','2026-11-01T10:00:00','2026-11-01T10:00:00')")
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        pulse_db._SCHEMA_READY.discard(self.db)
        self.tmp.cleanup()

    @staticmethod
    def as_user(perms, uid="1"):
        def auth(request, session, perm="can_view_dashboard", admin=False):
            if perm and not perms.get(perm):
                raise HTTPException(status_code=403, detail="nein")
            return SimpleNamespace(user={"id": uid, "global_name": f"M{uid}"}, guild=SimpleNamespace(id=1, name="G", members=[]), guild_name="G", perms=perms,
                                   config={}, member=None, msg="", msg_ok=True)
        return patch.object(ws, "auth", auth)

    @staticmethod
    def status(case_id="c1"):
        with pulse_ultimate.cx() as c:
            row = c.execute("SELECT status,closed_at FROM ultimate_cases WHERE id=?", (case_id,)).fetchone()
        return row["status"], row["closed_at"]

    @staticmethod
    def events(case_id="c1"):
        with pulse_ultimate.cx() as c:
            return [dict(r) for r in c.execute("SELECT event_type,details FROM ultimate_case_events WHERE case_id=? ORDER BY created_at", (case_id,)).fetchall()]


class LogicCase(Base):
    def test_validation(self):
        for bad in (("", LONG), ("nope", LONG), ("bestaetigt", "kurz"), ("bestaetigt", "x" * 3001), ("bestaetigt", LONG, "m" * 2001)):
            with self.assertRaises(ValueError, msg=bad):
                report.validate(*bad)
        self.assertEqual(report.validate("teilweise", f"  {LONG} ")["summary"], LONG)

    def test_save_closes_the_case_once_and_replaces_the_report(self):
        row = report.save("c1", "bestaetigt", LONG, "Verwarnung", "Regel klarer formulieren", "Ann, Bo", "9", "Lead")
        self.assertEqual(self.status()[0], "closed")
        closed_at = self.status()[1]
        self.assertIsNotNone(closed_at)
        report.save("c1", "teilweise", LONG + " Korrigiert.", actor_id="9", actor_name="Lead")
        self.assertEqual(report.get("c1")["outcome"], "teilweise")
        self.assertEqual(self.status()[1], closed_at)  # the first closing time is kept
        self.assertEqual(row["case_id"], "c1")
        with pulse_db.connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM case_closing_reports").fetchone()[0], 1)

    def test_unknown_case_and_invalid_input_change_nothing(self):
        with self.assertRaises(LookupError):
            report.save("missing", "bestaetigt", LONG)
        with self.assertRaises(ValueError):
            report.save("c1", "bestaetigt", "kurz")
        self.assertEqual((self.status()[0], report.get("c1")), ("open", None))

    def test_table_is_part_of_the_backup(self):
        report.save("c1", "bestaetigt", LONG)
        self.assertEqual(len(pulse_db.export_state()["case_closing_reports"]), 1)


class RouteCase(Base):
    FORM = {"outcome": "bestaetigt", "summary": LONG, "measures": "M", "lessons": "L", "participants": "P"}

    def test_leadership_writes_report_closes_case_and_it_is_in_timeline_and_audit(self):
        with self.as_user(LEAD):
            self.assertEqual(self.client.get("/ultimate/cases/c1/abschluss").status_code, 200)
            resp = self.client.post("/ultimate/cases/c1/abschluss", data=self.FORM, follow_redirects=False)
            self.assertEqual((resp.status_code, resp.headers["location"]), (303, "/ultimate/cases/c1"))
            detail = self.client.get("/ultimate/cases/c1").text
        self.assertEqual(self.status()[0], "closed")
        self.assertIn("Sachverhalt bestätigt", detail)
        self.assertIn("Bericht korrigieren", detail)
        self.assertIn("closing_report", [e["event_type"] for e in self.events()])
        self.assertTrue(any("Abschlussbericht" in str(e) for e in ws.load_json(ws.AUDIT_FILE, [])))

    def test_invalid_report_is_rejected_without_closing(self):
        with self.as_user(LEAD):
            resp = self.client.post("/ultimate/cases/c1/abschluss", data={**self.FORM, "summary": "zu kurz"}, follow_redirects=False)
        self.assertIn("/abschluss?msg=", resp.headers["location"])
        self.assertEqual((self.status()[0], report.get("c1")), ("open", None))

    def test_internal_notes_go_to_the_timeline_only_for_leadership(self):
        with self.as_user(LEAD):
            self.assertEqual(self.client.post("/ultimate/cases/c1/notiz", data={"text": "Rückfrage an Ann"}, follow_redirects=False).status_code, 303)
            self.assertEqual(self.client.post("/ultimate/cases/c1/notiz", data={"text": "  "}).status_code, 400)
            self.assertEqual(self.client.post("/ultimate/cases/nope/notiz", data={"text": "x"}).status_code, 404)
        self.assertIn(("internal_note", "Rückfrage an Ann"), [(e["event_type"], e["details"]) for e in self.events()])

    def test_normal_members_cannot_read_or_write_and_nothing_is_stored(self):
        with self.as_user(TEAM):
            for method, path, data in (("get", "/ultimate/cases/c1/abschluss", None), ("post", "/ultimate/cases/c1/abschluss", self.FORM),
                                       ("post", "/ultimate/cases/c1/notiz", {"text": "x"})):
                self.assertEqual(getattr(self.client, method)(path, **({"data": data} if data else {})).status_code, 403, path)
        self.assertEqual((self.status()[0], report.get("c1"), self.events()), ("open", None, []))

    def test_report_text_is_escaped_on_the_case_page(self):
        report.save("c1", "bestaetigt", "<script>alert(1)</script> viel Text hier", "<b>x</b>")
        with self.as_user(LEAD):
            detail = self.client.get("/ultimate/cases/c1").text
        self.assertNotIn("<script>alert(1)</script>", detail)


if __name__ == "__main__":
    unittest.main()
