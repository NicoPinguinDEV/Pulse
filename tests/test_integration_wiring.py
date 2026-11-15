"""Audit #117/#118/#119/#122: Alt-Schreiber laufen über denselben Vertrag (Inbox-Grenzen, Korrelation, Fall-Link)."""
import os
import tempfile
import unittest
from unittest.mock import patch

import pulse_db
import pulse_events as events


class WiringBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = f"{self.tmp.name}/pulse.db"
        patcher = patch.object(pulse_db, "DB_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        pulse_db._SCHEMA_READY.discard(path)
        pulse_db.init_db()


class NotifyContractTests(WiringBase):
    UID = "900000000000000002"

    def _row(self, nid):
        with pulse_db.connect() as db:
            return dict(db.execute("SELECT * FROM notifications WHERE id=?", (nid,)).fetchone())

    def test_external_and_protocol_relative_links_fall_back_to_the_inbox(self):
        for bad in ("https://evil.example/x", "//evil.example", "javascript:alert(1)", "/a\b"):
            self.assertEqual(self._row(pulse_db.notify(self.UID, "T", "B", url=bad))["url"], "/pulse-inbox", bad)

    def test_internal_links_and_normal_text_are_kept(self):
        row = self._row(pulse_db.notify(self.UID, "Titel", "Text", url="/tasks"))
        self.assertEqual((row["url"], row["title"], row["body"]), ("/tasks", "Titel", "Text"))

    def test_title_and_body_are_bounded(self):
        row = self._row(pulse_db.notify(self.UID, "T" * 500, "B" * 5000, url="/tasks"))
        self.assertEqual((len(row["title"]), len(row["body"])), (160, 1000))


class LegacyEventTests(WiringBase):
    def test_legacy_ticket_and_task_events_get_the_shared_correlation(self):
        pulse_db.record_event("ticket_closed", "ticket", "t-1", "1", "Ann", {"reason": "x"})
        pulse_db.record_event("task_completed", "task", "task-9", "1", "Ann", {"title": "A"})
        admin = {"is_admin": True}
        self.assertEqual(len(events.history(correlation_id="ticket:t-1", perms=admin)), 1)
        self.assertEqual(len(events.history(correlation_id="task:task-9", perms=admin)), 1)

    def test_other_targets_and_non_dict_payloads_stay_unchanged(self):
        import json
        pulse_db.record_event("something", "panel", "p1", "1", "Ann", {"a": 1})
        pulse_db.record_event("ticket_x", "ticket", "t-2", "1", "Ann", ["liste"])
        rows = {r["event_type"]: json.loads(r["payload_json"]) for r in pulse_db.events(10)}
        self.assertEqual(rows["something"], {"a": 1})
        self.assertEqual(rows["ticket_x"], ["liste"])

    def test_an_explicit_correlation_wins(self):
        pulse_db.record_event("ticket_closed", "ticket", "t-3", "1", "Ann", {}, correlation_id="ticket:eigene")
        self.assertEqual(len(events.history(correlation_id="ticket:eigene", perms={"is_admin": True})), 1)
        self.assertEqual(events.history(correlation_id="ticket:t-3", perms={"is_admin": True}), [])


class CaseLinkTests(unittest.TestCase):
    def test_dashboard_link_needs_a_base_url_and_a_valid_reference(self):
        from cogs.mod_cases import ModCases  # noqa: F401 - Klassenname prüfen
        link = ModCases._dashboard_link
        with patch.dict(os.environ, {"PUBLIC_BASE_URL": "https://pulse.example"}):
            self.assertIn("https://pulse.example/mod-cases/verlauf?ref=", link("BM:MOD-1A2B3C"))
            self.assertEqual(link("kaputt"), "")
        with patch.dict(os.environ, {"PUBLIC_BASE_URL": ""}):
            self.assertEqual(link("BM:MOD-1A2B3C"), "")


if __name__ == "__main__":
    unittest.main()
