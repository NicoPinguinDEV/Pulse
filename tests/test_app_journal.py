"""Audit #100: applicant communication journal (append-only, hash chain, failures recorded, retention, export, backup)."""
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_app_journal as journal
import pulse_db


class JournalCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.patch.start()
        pulse_db._SCHEMA_READY.discard(self.db_path)
        pulse_db.init_db()

    def tearDown(self):
        pulse_db._SCHEMA_READY.discard(self.db_path)
        self.patch.stop()
        self.tmp.cleanup()

    def add(self, app="A1", kind="received", outcome="sent", text="Hallo", **kw):
        if outcome == "failed":
            kw.setdefault("detail", "dm_not_delivered")
        return journal.record(app, kind, outcome, text, **kw)

    def sql(self, statement, *args):
        con = sqlite3.connect(self.db_path)
        try:
            con.execute(statement, args)
            con.commit()
        finally:
            con.close()


class AppendAndReadTests(JournalCase):
    def test_entries_are_numbered_chained_and_verifiable(self):
        a = self.add(kind="received", actor_id="0", actor_name="System")
        b = self.add(kind="scheduled", text="Termin", actor_id="7", actor_name="Anna", template_ref="legacy:scheduled")
        c = self.add(kind="reminder", outcome="failed", text="Erinnerung", detail="user_not_found")
        self.assertEqual([x["seq"] for x in (a, b, c)], [1, 2, 3])
        self.assertEqual(a["prev_hash"], journal.GENESIS)
        self.assertEqual((b["prev_hash"], c["prev_hash"]), (a["entry_hash"], b["entry_hash"]))
        self.assertEqual(journal.verify("A1"), {"ok": True, "count": 3, "broken_at": None, "reason": "", "head": c["entry_hash"]})
        listed = journal.entries("A1")
        self.assertEqual([e["seq"] for e in listed], [3, 2, 1])
        self.assertEqual([e["seq"] for e in journal.entries("A1", newest_first=False)], [1, 2, 3])
        self.assertEqual((listed[0]["outcome"], listed[0]["detail"]), ("failed", "user_not_found"))
        self.assertEqual(listed[1]["actor_name"], "Anna")

    def test_failed_messages_are_recorded_with_a_fixed_reason(self):
        e = self.add(outcome="failed", detail="bot_unavailable", text="Nicht zugestellt")
        self.assertEqual((e["outcome"], e["detail"]), ("failed", "bot_unavailable"))
        self.assertEqual(self.add()["detail"], "")  # a successful entry never carries a reason

    def test_applications_have_independent_chains(self):
        self.add("A1")
        self.add("A2")
        self.add("A1", kind="hired")
        self.assertEqual([e["seq"] for e in journal.entries("A2")], [1])
        self.assertTrue(journal.verify("A1")["ok"] and journal.verify("A2")["ok"])
        self.assertEqual(journal.verify("A3"), {"ok": True, "count": 0, "broken_at": None, "reason": "", "head": journal.GENESIS})

    def test_text_is_bounded_and_hashed(self):
        e = self.add(text="x" * 5000)
        self.assertEqual(len(e["text"]), journal.MAX_TEXT)
        self.assertEqual(e["text_sha256"], hashlib.sha256(("x" * journal.MAX_TEXT).encode()).hexdigest())

    def test_invalid_input_is_rejected_and_nothing_is_written(self):
        bad = [dict(app_id=""), dict(app_id="x" * 81), dict(kind="spam"), dict(outcome="maybe"), dict(channel="sms"),
               dict(outcome="failed", detail="Traceback (most recent call last)"), dict(outcome="failed", detail="")]
        for kw in bad:
            args = {"app_id": "A1", "kind": "received", "outcome": "sent", "text": "x", **kw}
            with self.subTest(kw=kw), self.assertRaises(ValueError):
                journal.record(args.pop("app_id"), args.pop("kind"), args.pop("outcome"), args.pop("text"), **args)
        self.assertEqual(journal.entries("A1"), [])

    def test_record_safe_never_raises(self):
        with self.assertLogs("pulse.app_journal", "WARNING"):
            self.assertIsNone(journal.record_safe("A1", "spam", "sent", "x"))
        self.assertIsNotNone(journal.record_safe("A1", "received", "sent", "x"))
        with patch.object(pulse_db, "connect", side_effect=RuntimeError("db weg")), self.assertLogs("pulse.app_journal", "WARNING"):
            self.assertIsNone(journal.record_safe("A1", "received", "sent", "x"))

    def test_racing_writers_get_distinct_sequence_numbers(self):
        self.add()
        real = journal.entry_hash
        calls = {"n": 0}

        def racing(row):
            calls["n"] += 1
            if calls["n"] == 1:  # another process takes seq 2 between our read of the head and our insert
                self.sql("INSERT INTO application_comm_journal (id,app_id,seq,at,channel,kind,outcome,detail,actor_id,actor_name,"
                         "template_ref,text,text_sha256,prev_hash,entry_hash) VALUES "
                         "('other','A1',2,'t','dm','scheduled','sent','','','','','x','h','p','e')")
            return real(row)
        with patch.object(journal, "entry_hash", racing):
            e = self.add(kind="hired")
        self.assertEqual(e["seq"], 3)  # retried on the new head
        self.assertEqual([x["seq"] for x in journal.entries("A1", newest_first=False)], [1, 2, 3])


class TamperEvidenceTests(JournalCase):
    def setUp(self):
        super().setUp()
        for kind in ("received", "scheduled", "reminder", "hired"):
            self.add(kind=kind, text=f"Text {kind}")

    def test_edited_text_is_detected(self):
        self.sql("UPDATE application_comm_journal SET text='Anders' WHERE seq=2")
        verdict = journal.verify("A1")
        self.assertEqual((verdict["ok"], verdict["broken_at"], verdict["reason"]), (False, 2, "Text verändert"))

    def test_every_edited_field_is_detected(self):
        for column, value in (("outcome", "failed"), ("actor_id", "666"), ("at", "2020-01-01T00:00:00+00:00"),
                              ("kind", "rejected"), ("detail", "error"), ("template_ref", "x"), ("channel", "mail")):
            with self.subTest(column=column):
                self.sql(f"UPDATE application_comm_journal SET {column}=? WHERE seq=3", value)
                verdict = journal.verify("A1")
                self.assertFalse(verdict["ok"])
                self.assertEqual(verdict["broken_at"], 3)
                self.tearDown()
                self.setUp()

    def test_removed_middle_entry_is_detected(self):
        self.sql("DELETE FROM application_comm_journal WHERE seq=2")
        verdict = journal.verify("A1")
        self.assertEqual((verdict["ok"], verdict["broken_at"]), (False, 3))

    def test_rewritten_entry_with_recomputed_hash_breaks_the_next_link(self):
        row = journal.entries("A1", newest_first=False)[1]
        forged = {**row, "outcome": "failed", "detail": "error"}
        self.sql("UPDATE application_comm_journal SET outcome='failed', detail='error', entry_hash=? WHERE seq=2",
                 journal.entry_hash(forged))
        verdict = journal.verify("A1")
        self.assertEqual((verdict["ok"], verdict["broken_at"], verdict["reason"]), (False, 3, "Verkettung unterbrochen"))

    def test_redacted_text_still_verifies_through_its_hash(self):
        self.sql("UPDATE application_comm_journal SET text=NULL WHERE seq=2")
        self.assertTrue(journal.verify("A1")["ok"])


class RetentionExportBackupTests(JournalCase):
    def test_purge_removes_only_the_given_applications(self):
        self.add("A1")
        self.add("A1", kind="hired")
        self.add("A2")
        self.assertEqual(journal.purge(["A1", "", None]), 2)
        self.assertEqual(journal.entries("A1"), [])
        self.assertEqual(len(journal.entries("A2")), 1)
        self.assertEqual(journal.purge([]), 0)

    def test_export_groups_entries_per_application_oldest_first(self):
        self.add("A1")
        self.add("A1", kind="hired")
        self.add("A2")
        out = journal.export_for(["A1", "A3"])
        self.assertEqual(list(out), ["A1"])
        self.assertEqual([e["kind"] for e in out["A1"]], ["received", "hired"])
        self.assertEqual(journal.export_for([]), {})

    def test_table_is_part_of_the_backup_and_restore(self):
        self.add()
        self.add(kind="hired")
        state = pulse_db.export_state()
        self.assertEqual(len(state["application_comm_journal"]), 2)
        journal.purge(["A1"])
        pulse_db.import_state(state)
        self.assertEqual(journal.verify("A1")["count"], 2)
        self.assertTrue(journal.verify("A1")["ok"])


class JournalRouteAndRetentionCase(JournalCase):
    def setUp(self):
        super().setUp()
        import webserver as ws
        from tests.test_interview_calendar import make_auth
        self.ws, self.make_auth = ws, make_auth
        root = Path(self.tmp.name)
        self.patches = [patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(ws, "APPS_FILE", str(root / "applications.json")),
                        patch.object(ws, "compute_perms", lambda guild, uid, cfg: ({}, None))]
        for p in self.patches:
            p.start()
        ws.save_json(ws.APPS_FILE, {"a1": {"user_id": "100", "name": "X", "status": "pending"}})
        from fastapi.testclient import TestClient
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in self.patches:
            p.stop()
        super().tearDown()

    def get(self, app_id, user):
        with patch.object(self.ws, "auth", self.make_auth(user)):
            return self.client.get(f"/applications/journal/{app_id}")

    def test_staff_sees_entries_and_chain_status(self):
        self.add("a1", text="Hallo Bewerber <b>")
        resp = self.get("a1", "2")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Hallo Bewerber &lt;b&gt;", resp.text)
        self.assertIn("unverändert", resp.text)

    def test_broken_chain_is_shown(self):
        self.add("a1")
        self.sql("UPDATE application_comm_journal SET outcome='failed' WHERE app_id='a1'")
        self.assertIn("Prüfung fehlgeschlagen", self.get("a1", "2").text)

    def test_non_recruiter_is_denied_and_unknown_app_is_404(self):
        self.add("a1")
        self.assertEqual(self.get("a1", "3").status_code, 403)
        self.assertEqual(self.get("nope", "2").status_code, 404)

    def test_retention_removes_journal_with_expired_application_only(self):
        import pulse_retention
        self.add("old")
        self.add("keep")
        ws = self.ws
        ws.save_json(ws.APPS_FILE, {"old": {"status": "rejected", "decided_at": "01.01.2020 10:00"},
                                    "keep": {"status": "pending"}})
        settings = {"enabled": True, "applications_days": 30, "audit_days": 730}
        with patch.object(pulse_retention, "recent_full_backup", return_value="b.zip"),                 patch.object(pulse_retention, "load_settings", return_value=settings),                 patch.object(pulse_retention, "save_settings"), patch.object(ws, "log_audit"),                 patch.object(pulse_retention, "RETENTION_MUTATIONS_ENABLED", True):
            report = pulse_retention.run(ws, force=True)
        self.assertEqual(report.get("journal_entries"), 1)
        self.assertEqual(journal.verify("old")["count"], 0)
        self.assertEqual(journal.verify("keep")["count"], 1)

    def test_personal_export_contains_journal(self):
        import pulse_retention
        self.add("a1")
        self.assertEqual(len(pulse_retention._journal_export({"a1": {}})["a1"]), 1)


if __name__ == "__main__":
    unittest.main()
