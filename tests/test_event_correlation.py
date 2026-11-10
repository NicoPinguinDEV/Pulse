"""Phase 17: common correlation IDs for events emitted by different Pulse surfaces."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db


class EventCorrelationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.db_patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.db_patch.start()
        pulse_db._SCHEMA_READY.discard(self.db_path)
        pulse_db.init_db()

    def tearDown(self):
        pulse_db._SCHEMA_READY.discard(self.db_path)
        self.db_patch.stop()
        self.tmp.cleanup()

    def _payload(self, event_id):
        with pulse_db.connect() as db:
            row = db.execute("SELECT payload_json FROM events WHERE id=?", (event_id,)).fetchone()
        self.assertIsNotNone(row)
        return json.loads(row["payload_json"])

    def test_correlation_id_is_added_without_mutating_original_payload(self):
        payload = {"request_id": "ACT-1234", "details": "Ticket übernommen"}
        event_id = pulse_db.record_event(
            "ticket_handover", "suite", "T-123", "42", "Teamler", payload,
            correlation_id="ACT-1234",
        )
        stored = self._payload(event_id)
        self.assertEqual(stored["request_id"], "ACT-1234")
        self.assertEqual(stored["details"], "Ticket übernommen")
        self.assertEqual(stored["correlation_id"], "ACT-1234")
        self.assertNotIn("correlation_id", payload)

    def test_legacy_event_payload_is_unchanged_without_correlation_id(self):
        payload = {"action": "updated", "value": 3}
        event_id = pulse_db.record_event("legacy", "panel", "", "7", "Admin", payload)
        self.assertEqual(self._payload(event_id), payload)

    def test_non_object_payload_is_wrapped_only_when_correlation_is_requested(self):
        event_id = pulse_db.record_event("note", payload="legacy text", correlation_id="flow-1")
        self.assertEqual(self._payload(event_id), {"value": "legacy text", "correlation_id": "flow-1"})

    def test_correlation_id_is_trimmed_and_bounded(self):
        event_id = pulse_db.record_event("test", correlation_id="  " + "x" * 200)
        payload = self._payload(event_id)
        self.assertEqual(payload["correlation_id"], "x" * 128)


if __name__ == "__main__":
    unittest.main()
