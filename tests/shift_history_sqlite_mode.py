"""Phase 14.7: run existing test modules with the shift history in SQLite instead of shifts.json.

    python -m tests.shift_history_sqlite_mode tests.test_dienst tests.test_shift_confirmation …

Every write of shifts.json in JSON mode (the tests seed their data like that) is followed by the real
migration, so the code under test reads and writes the history through pulse.db. pulse_db.DB_PATH and
BACKUP_DIR point to a temporary folder unless a test patches them itself. Not part of unittest discovery
(the file name does not start with test_); tests/test_shift_history_sqlite.py runs it for the shift suites.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_shift_history as store
import webserver as ws


def run(module_names, verbosity=1) -> unittest.TestResult:
    real_write = ws._write_json_file
    migrated = {"count": 0}

    def write_then_migrate(path, data):
        real_write(path, data)
        if (os.path.abspath(path) == os.path.abspath(ws.SHIFTS_FILE) and isinstance(data, dict)
                and store.MARKER_KEY not in data):
            report = store.migrate(ws)
            if report["status"] != "migrated":
                raise AssertionError(f"Migration im Testlauf fehlgeschlagen: {report}")
            migrated["count"] += 1

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
            patch.object(pulse_db, "DB_PATH", str(Path(tmp) / "pulse.db")), \
            patch.object(ws, "BACKUP_DIR", str(Path(tmp) / "backups")), \
            patch.object(ws, "_write_json_file", write_then_migrate):
        suite = unittest.defaultTestLoader.loadTestsFromNames(module_names)
        result = unittest.TextTestRunner(verbosity=verbosity, stream=sys.stderr).run(suite)
    result.migrations = migrated["count"]
    return result


if __name__ == "__main__":
    res = run(sys.argv[1:] or ["tests.test_dienst"])
    print(f"Migrationen im Lauf: {res.migrations}")
    sys.exit(0 if res.wasSuccessful() else 1)
