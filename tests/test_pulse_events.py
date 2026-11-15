"""Audit #116/#122/#125: event contract (validation, legacy classification, case-linked history, rights)."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_events as ev

MOD = {"can_moderate_kicks": True}
ADMIN = {"is_admin": True}
NONE = {"can_view_dashboard": True}


class BuildPayloadTests(unittest.TestCase):
    def test_envelope_and_event_type(self):
        payload, event_type = ev.build_payload("ticket", "closed", {"reason": "erledigt"})
        self.assertEqual(event_type, "ticket.closed")
        self.assertEqual(payload, {"schema": 1, "domain": "ticket", "action": "closed", "reason": "erledigt"})

    def test_case_ref_is_canonical_and_becomes_correlation_id(self):
        payload, _ = ev.build_payload("moderation", "created", case_ref="mod-1a2b3c")
        self.assertEqual(payload["case_ref"], "BM:MOD-1A2B3C")
        self.assertEqual(payload["correlation_id"], "BM:MOD-1A2B3C")

    def test_explicit_correlation_id_wins_and_is_bounded(self):
        payload, _ = ev.build_payload("task", "created", case_ref="TW:warn_ab12cd", correlation_id="x" * 300)
        self.assertEqual(payload["correlation_id"], "x" * 128)
        self.assertEqual(payload["case_ref"], "TW:warn_ab12cd")

    def test_invalid_input_is_rejected(self):
        bad = [
            dict(domain="nope", action="x"),
            dict(domain="ticket", action="Closed"),
            dict(domain="ticket", action="a b"),
            dict(domain="ticket", action=""),
            dict(domain="ticket", action="x", payload="text"),
            dict(domain="ticket", action="x", payload={"schema": 9}),
            dict(domain="ticket", action="x", payload={"correlation_id": "evil"}),
            dict(domain="ticket", action="x", case_ref="garbage"),
            dict(domain="ticket", action="x", payload={"blob": "a" * 9000}),
        ]
        for kwargs in bad:
            with self.subTest(kwargs=list(kwargs)):
                with self.assertRaises(ValueError):
                    ev.build_payload(kwargs.pop("domain"), kwargs.pop("action"), kwargs.pop("payload", None), **kwargs)

    def test_caller_payload_is_not_mutated(self):
        original = {"a": 1}
        ev.build_payload("task", "created", original, case_ref="KI:abcdef1234")
        self.assertEqual(original, {"a": 1})


class ClassifyTests(unittest.TestCase):
    def test_new_types_read_as_written(self):
        self.assertEqual(ev.classify("ticket.closed"), ("ticket", "closed"))
        self.assertEqual(ev.classify("recruiting.interview_scheduled"), ("recruiting", "interview_scheduled"))

    def test_legacy_slugs_by_prefix(self):
        self.assertEqual(ev.classify("ticket_closed_dashboard", "ticket"), ("ticket", "ticket_closed_dashboard"))
        self.assertEqual(ev.classify("task_created", "task"), ("task", "task_created"))
        self.assertEqual(ev.classify("team_warn_abuse_detected", "team_member")[0], "team")
        self.assertEqual(ev.classify("status_changed", "user")[0], "team")

    def test_legacy_display_text_by_target(self):
        self.assertEqual(ev.classify("Datenschutzanfrage eingegangen", "privacy_request"),
                         ("privacy", "datenschutzanfrage_eingegangen"))
        self.assertEqual(ev.classify("Automation: Protokoll", "automation"), ("automation", "automation_protokoll"))
        self.assertEqual(ev.classify("audit", "panel"), ("panel", "audit"))

    def test_unknown_falls_back_to_other_without_failing(self):
        self.assertEqual(ev.classify("AutoMod-Maßnahme", "user"), ("other", "automod_massnahme"))
        self.assertEqual(ev.classify(None, None), ("other", "unknown"))
        self.assertEqual(ev.classify("nope.thing", ""), ("other", "nope_thing"))


class DbTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.patch.start()
        pulse_db._SCHEMA_READY.discard(self.db_path)
        pulse_db.init_db()

    def tearDown(self):
        pulse_db._SCHEMA_READY.discard(self.db_path)
        self.patch.stop()
        self.tmp.cleanup()

    def _row(self, event_id):
        with pulse_db.connect() as db:
            return dict(db.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone())

    def test_emit_stores_contract_event_and_describe_roundtrips(self):
        eid = ev.emit("moderation", "created", target_type="user", target_id="42", actor_id="7", actor_name="Mod",
                      payload={"reason": "Spam"}, case_ref="BM:MOD-1A2B3C")
        row = self._row(eid)
        self.assertEqual(row["event_type"], "moderation.created")
        info = ev.describe(row)
        self.assertFalse(info["legacy"])
        self.assertEqual((info["domain"], info["action"], info["case_ref"]), ("moderation", "created", "BM:MOD-1A2B3C"))
        self.assertEqual(info["payload"]["reason"], "Spam")

    def test_legacy_row_is_described_but_marked_legacy(self):
        eid = pulse_db.record_event("ticket_closed", "ticket", "T1", "7", "Mod", {"reason": "x"})
        info = ev.describe(self._row(eid))
        self.assertTrue(info["legacy"])
        self.assertEqual(info["domain"], "ticket")
        self.assertEqual(info["correlation_id"], "ticket:T1")  # #122: Alt-Schreiber erhalten die gemeinsame Korrelation

    def test_describe_survives_broken_payload(self):
        row = {"id": "e", "event_type": "x", "payload_json": "{kaputt", "created_at": "t"}
        self.assertEqual(ev.describe(row)["payload"], {})
        self.assertEqual(ev.describe({**row, "payload_json": "[1]"})["payload"], {"value": [1]})

    def test_existing_record_event_is_unchanged(self):
        eid = pulse_db.record_event("legacy", "panel", "", "7", "Admin", {"a": 1})
        self.assertEqual(json.loads(self._row(eid)["payload_json"]), {"a": 1})

    def test_history_links_events_of_one_case_across_domains(self):
        ref = "BM:MOD-1A2B3C"
        ev.emit("moderation", "created", case_ref=ref)
        ev.emit("task", "created", case_ref=ref, payload={"title": "Nachfassen"})
        ev.emit("moderation", "created", case_ref="BM:MOD-FFFFFF")
        pulse_db.record_event("ticket_closed", "ticket", "T1", "7", "Mod", {"reason": "x"})
        found = ev.history(case_ref=ref, perms=MOD)
        self.assertEqual({e["domain"] for e in found}, {"moderation", "task"})
        self.assertEqual(len(found), 2)
        self.assertEqual([e["domain"] for e in ev.history(case_ref=ref, perms=MOD, domain="task")], ["task"])

    def test_history_is_fail_closed(self):
        ev.emit("moderation", "created", case_ref="BM:MOD-1A2B3C")
        for perms in (None, {}, NONE, {"can_warn": True}, {"is_admin": "yes"}):
            with self.subTest(perms=perms), self.assertRaises(PermissionError):
                ev.history(case_ref="BM:MOD-1A2B3C", perms=perms)
        self.assertEqual(len(ev.history(case_ref="BM:MOD-1A2B3C", perms=ADMIN)), 1)

    def test_history_by_bare_correlation_id_needs_admin(self):
        ev.emit("task", "created", correlation_id="flow-1")
        with self.assertRaises(PermissionError):
            ev.history(correlation_id="flow-1", perms=MOD)
        self.assertEqual(len(ev.history(correlation_id="flow-1", perms=ADMIN)), 1)

    def test_history_requires_a_filter_and_validates_input(self):
        with self.assertRaises(ValueError):
            ev.history(perms=ADMIN)
        with self.assertRaises(ValueError):
            ev.history(case_ref="garbage", perms=ADMIN)
        with self.assertRaises(ValueError):
            ev.history(correlation_id="x", domain="nope", perms=ADMIN)

    def test_history_limit_is_bounded(self):
        for _ in range(5):
            ev.emit("task", "created", correlation_id="many")
        self.assertEqual(len(ev.history(correlation_id="many", perms=ADMIN, limit=2)), 2)
        self.assertEqual(len(ev.history(correlation_id="many", perms=ADMIN, limit=-5)), 1)

    def test_emit_safe_never_raises(self):
        self.assertIsNone(ev.emit_safe("nope", "x"))
        def boom(*a):
            raise RuntimeError("db weg")
        self.assertIsNone(ev.emit_safe("task", "created", recorder=boom))
        self.assertIsNotNone(ev.emit_safe("task", "created"))


if __name__ == "__main__":
    unittest.main()
