"""M-D1: the legacy restore (/ultimate/backup/{name}/restore → pulse_next.restore_backup) must be as safe as the
full restore: broken JSON is refused before anything is written, a safety copy of the current state is taken,
a second restore cannot run at the same time, and a failure mid-way puts the previous state back.

Before the fix the legacy path wrote every member straight over the live files (no validation, no safety copy,
no lock). Only temp data – never the real runtime files.
"""
import json
import os
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import pulse_backup
import pulse_db
import pulse_next
import pulse_ultimate


class LegacyRestoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.base = Path(self.tmp.name)
        self.db_path = self.base / "pulse.db"
        con = sqlite3.connect(self.db_path)
        con.execute("CREATE TABLE marker(v TEXT)")
        con.execute("INSERT INTO marker VALUES('live')")
        con.commit()
        con.close()
        (self.base / "config.json").write_text(json.dumps({"state": "live"}), encoding="utf-8")
        self.patches = [
            patch.object(pulse_next, "BASE", self.base),
            patch.object(pulse_next, "BACKUP_DIR", self.base / "backups"),
            patch.object(pulse_ultimate, "ATTACHMENTS_DIR", self.base / "attachments"),
            patch.object(pulse_db, "DB_PATH", str(self.db_path)),
            patch.object(pulse_next, "setup_next", lambda: None),
            patch.dict(os.environ, {"PULSE_DB_PATH": str(self.db_path)}),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def legacy_zip(self, members: dict) -> Path:
        src_db = self.base / "backup_src.db"
        con = sqlite3.connect(src_db)
        con.execute("CREATE TABLE IF NOT EXISTS marker(v TEXT)")
        con.execute("DELETE FROM marker")
        con.execute("INSERT INTO marker VALUES('restored')")
        con.commit()
        con.close()
        path = self.base / "pulse_backup_20261024_120000.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.write(src_db, "pulse.db")
            for name, data in members.items():
                z.writestr(name, data)
        src_db.unlink()
        return path

    def live_marker(self):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute("SELECT v FROM marker").fetchone()[0]
        finally:
            con.close()

    def live_config(self):
        return json.loads((self.base / "config.json").read_text(encoding="utf-8"))

    def test_broken_json_is_refused_before_anything_is_written(self):
        path = self.legacy_zip({"config.json": "{kaputt"})
        with self.assertRaises(HTTPException) as caught:
            pulse_next.restore_backup(path)
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(self.live_config(), {"state": "live"})
        self.assertEqual(self.live_marker(), "live")

    def test_valid_restore_keeps_a_safety_copy_of_the_previous_state(self):
        path = self.legacy_zip({"config.json": json.dumps({"state": "restored"})})
        self.assertTrue(pulse_next.restore_backup(path))
        self.assertEqual(self.live_config(), {"state": "restored"})
        self.assertEqual(self.live_marker(), "restored")
        safety = [p for p in (self.base / "backups").glob("pulse_full_pre_restore_*.zip")]
        self.assertEqual(len(safety), 1, "vor dem Überschreiben muss eine Sicherheitskopie entstehen")
        self.assertEqual(pulse_backup.verify_full_backup(safety[0]), [])
        self.assertFalse(pulse_backup.maintenance_active())

    def test_second_restore_at_the_same_time_is_refused(self):
        path = self.legacy_zip({"config.json": json.dumps({"state": "restored"})})
        self.assertTrue(pulse_backup._RESTORE_LOCK.acquire(blocking=False))
        try:
            with self.assertRaises(HTTPException) as caught:
                pulse_next.restore_backup(path)
            self.assertEqual(caught.exception.status_code, 409)
        finally:
            pulse_backup._RESTORE_LOCK.release()
        self.assertEqual(self.live_config(), {"state": "live"})

    def test_failure_mid_way_puts_the_previous_state_back(self):
        path = self.legacy_zip({"config.json": json.dumps({"state": "restored"}),
                                "team_data.json": json.dumps({"x": 1})})
        real_replace = os.replace

        def failing_replace(src, dst):
            if str(dst).endswith("team_data.json"):
                raise OSError("Datenträger voll")
            return real_replace(src, dst)

        with patch.object(pulse_next.os, "replace", failing_replace):
            with self.assertRaises(HTTPException) as caught:
                pulse_next.restore_backup(path)
        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(self.live_config(), {"state": "live"})
        self.assertEqual(self.live_marker(), "live")
        self.assertFalse(pulse_backup.maintenance_active())


if __name__ == "__main__":
    unittest.main()
