"""D-10: consistency diagnosis finds orphaned/contradicting data and only reports it. D-07: growth watch."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_consistency
import pulse_db
import webserver as ws

MEMBER = SimpleNamespace(id=10, display_name="Max", roles=[SimpleNamespace(id=ws.WARN_ROLE_IDS[2])])
CHANNEL = SimpleNamespace(id=500)
GUILD = SimpleNamespace(get_member=lambda uid: MEMBER if uid == 10 else None,
                        get_channel=lambda cid: CHANNEL if cid == 500 else None)


class ConsistencyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")),
                        patch.object(ws, "SHIFTS_FILE", str(base / "shifts.json")),
                        patch.object(ws, "DATA_FILE", str(base / "team_data.json")),
                        patch.object(ws, "CONFIG_FILE", str(base / "config.json")),
                        patch.object(ws, "get_loas", lambda: {"10": {"von": "2026-10-10", "bis": "2026-10-01", "user_name": "Max"}})]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {"10": {}, "99": {}}, "history": []})
        ws.save_json(ws.DATA_FILE, {"10": {"warns_list": [{"id": "w1", "active": True, "expires_at": "2099-01-01T00:00:00+00:00"}]}})
        pulse_db.create_ticket(999, 1, 10, "Max", "support")   # channel 999 does not exist
        pulse_db.create_ticket(500, 1, 10, "Max", "support")
        pulse_db.set_setting("voice_support_cases", {"waiting:99": {"case_id": "c9", "user_id": 99}})
        self.base = base

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def test_findings_are_reported_not_fixed(self):
        before = ws.load_json(ws.SHIFTS_FILE, {})
        results = {r["area"]: r for r in pulse_consistency.check(ws, GUILD)}
        self.assertIn("99", results["Aktive Schichten"]["text"])
        self.assertEqual(results["Offene Tickets"]["count"], 1)
        self.assertIn("1 aktive Warnung", results["Warnrollen"]["text"])  # holds level-2 role with one warning
        self.assertFalse(results["Abmeldungen"]["ok"])
        self.assertIn("c9", results["Voice-Support-Fälle"]["text"])
        self.assertEqual(ws.load_json(ws.SHIFTS_FILE, {}), before)

    def test_clean_state_is_ok_and_growth_watch(self):
        MEMBER.roles = [SimpleNamespace(id=ws.WARN_ROLE_IDS[1])]
        try:
            results = {r["area"]: r for r in pulse_consistency.check(ws, GUILD)}
            self.assertTrue(results["Warnrollen"]["ok"])
        finally:
            MEMBER.roles = [SimpleNamespace(id=ws.WARN_ROLE_IDS[2])]
        (self.base / "logs.json").write_bytes(b"[" + b" " * (pulse_consistency.GROWTH_WARN_BYTES + 1) + b"]")
        growth = {g["name"]: g for g in pulse_consistency.growth(self.base)}
        self.assertFalse(growth["logs.json"]["ok"])
        self.assertTrue(growth["shifts.json"]["ok"])


if __name__ == "__main__":
    unittest.main()
