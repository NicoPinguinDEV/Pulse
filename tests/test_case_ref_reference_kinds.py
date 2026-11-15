"""Audit #116: Tickets (TK) und Spielermeldungen (SM) tragen dieselbe typisierte Referenz wie Moderationsfälle."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_case_evidence as ev
import pulse_case_registry as reg
import pulse_db
import pulse_player_reports as reports

TICKET_MGR = {"can_manage_tickets": True}
ANALYTICS = {"can_view_analytics": True}
MOD = {"can_moderate_kicks": True}
ADMIN = {"is_admin": True}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = f"{self.tmp.name}/pulse.db"
        patchers = [patch.object(pulse_db, "DB_PATH", path), patch.object(reports, "DB_PATH", f"{self.tmp.name}/pulse_suite.db")]
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)
        pulse_db._SCHEMA_READY.discard(path)
        pulse_db.init_db()
        self.ticket = pulse_db.create_ticket("c1", "100", "900000000000000001", "Spieler", "Support", "high")
        ticket_id = self.ticket["id"] if isinstance(self.ticket, dict) else self.ticket
        self.tid = ticket_id
        created = reports.create_report(100, 900000000000000002, "Reporter", "Täter", "rule_violation",
                                        "Eine ausreichend lange Beschreibung des Vorfalls.", "normal", None)
        self.rid = created["id"] if isinstance(created, dict) else created


class ReferenceKindTests(Base):
    def test_parse_and_bare_report_id(self):
        self.assertEqual(reg.parse_ref(f"TK:{self.tid}"), ("TK", self.tid))
        self.assertEqual(reg.parse_ref(self.rid.lower()), ("SM", self.rid))
        self.assertEqual(reg.parse_ref(f"SM:{self.rid.lower()}"), ("SM", self.rid))
        self.assertEqual(reg.make_ref("TK", self.tid), f"TK:{self.tid}")

    def test_ticket_resolves_only_with_the_ticket_right_and_in_its_guild(self):
        entry = reg.resolve(f"TK:{self.tid}", TICKET_MGR, guild_id=100)
        self.assertEqual((entry["kind"], entry["person_id"], entry["url"]), ("TK", "900000000000000001", f"/tickets/transcript/{self.tid}/view"))
        self.assertIsNone(reg.resolve(f"TK:{self.tid}", MOD, guild_id=100))
        self.assertIsNone(reg.resolve(f"TK:{self.tid}", {}, guild_id=100))
        self.assertIsNone(reg.resolve(f"TK:{self.tid}", TICKET_MGR, guild_id=999))
        self.assertIsNotNone(reg.resolve(f"TK:{self.tid}", ADMIN, guild_id=100))
        self.assertIsNone(reg.resolve("TK:gibtesnicht", ADMIN, guild_id=100))

    def test_report_resolves_for_analytics_viewers_and_does_not_link_a_person(self):
        entry = reg.resolve(self.rid, ANALYTICS, guild_id=100)
        self.assertEqual((entry["kind"], entry["person_id"], entry["url"]), ("SM", "", "/ultimate/reports"))
        self.assertIsNone(reg.resolve(self.rid, TICKET_MGR, guild_id=100))
        self.assertIsNone(reg.resolve(self.rid, ANALYTICS, guild_id=999))
        self.assertIsNone(reg.resolve("RPT-000000000000", ADMIN, guild_id=100))

    def test_person_history_and_old_gates_are_unchanged(self):
        self.assertEqual(reg.allowed_kinds(TICKET_MGR), set())               # kein Moderationsrecht
        self.assertEqual(reg.allowed_kinds(TICKET_MGR, include_reference_only=True), {"TK"})
        self.assertEqual(reg.allowed_kinds(ADMIN), {"BM", "KI", "TW", "TF"})
        history = reg.person_history("900000000000000001", ADMIN, guild_id=100)
        self.assertEqual(history["kinds"], ["BM", "TF", "TW", "KI"])
        self.assertFalse(any(e["kind"] in ("TK", "SM") for e in history["entries"]))

    def test_moderation_evidence_is_not_available_for_these_references(self):
        self.assertIsNone(ev.evidence_for(f"TK:{self.tid}", ADMIN, guild_id=100))
        self.assertIsNone(ev.evidence_for(self.rid, ADMIN, guild_id=100))


if __name__ == "__main__":
    unittest.main()
