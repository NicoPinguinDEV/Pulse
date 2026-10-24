"""Privacy-aware dashboard login IP audit tests."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_ip_audit


class DashboardIpAuditTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.path_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.path_patch.stop()
        self.tmp.cleanup()

    def test_only_literal_ip_addresses_are_recorded(self):
        self.assertFalse(pulse_ip_audit.record_login("123456789012345678", "Test", "forged, 1.2.3.4"))
        self.assertFalse(pulse_ip_audit.record_login("123456789012345678", "Test", "not-an-ip"))
        self.assertTrue(pulse_ip_audit.record_login("123456789012345678", "Test", "2001:db8::1", "Browser", now=1000))
        rows = pulse_ip_audit.list_entries(now=1001)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ip_address"], "2001:db8::1")
        self.assertEqual(rows[0]["user_id"], "123456789012345678")

    def test_retention_deletes_old_entries(self):
        pulse_ip_audit.record_login("123456789012345678", "Old", "192.0.2.2", now=1000)
        pulse_ip_audit.record_login("123456789012345679", "New", "192.0.2.3", now=5000000)
        rows = pulse_ip_audit.list_entries(now=5000000)
        self.assertEqual([row["username"] for row in rows], ["New"])

    def test_delete_user_history(self):
        pulse_ip_audit.record_login("123456789012345678", "Test", "192.0.2.2")
        self.assertEqual(pulse_ip_audit.delete_user("123456789012345678"), 1)
        self.assertEqual(pulse_ip_audit.list_entries(), [])


if __name__ == "__main__":
    unittest.main()
