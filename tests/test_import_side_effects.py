"""A-04: importing webserver (and the modules it registers) creates no runtime folders."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PROBE = r"""
import json, os, pathlib, sys
sys.path.insert(0, sys.argv[1])
calls = []
_mkdir, _makedirs = pathlib.Path.mkdir, os.makedirs
pathlib.Path.mkdir = lambda self, *a, **k: calls.append(str(self))
os.makedirs = lambda name, *a, **k: calls.append(str(name))
import webserver  # noqa: F401
import pulse_ultimate, pulse_next  # noqa: F401,E401
print("CALLS=" + json.dumps(calls))
"""


class ImportSideEffectTests(unittest.TestCase):
    def test_import_creates_no_attachments_or_backup_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = dict(os.environ, PULSE_DB_PATH=str(Path(tmp) / "pulse.db"), PYTHONIOENCODING="utf-8")
            result = subprocess.run([sys.executable, "-c", PROBE, str(ROOT)], cwd=tmp, env=env,
                                    capture_output=True, text=True, encoding="utf-8", timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        line = next(x for x in result.stdout.splitlines() if x.startswith("CALLS="))
        created = [Path(p).name for p in json.loads(line[len("CALLS="):])]
        self.assertNotIn("attachments", created)
        self.assertNotIn("backups", created)


if __name__ == "__main__":
    unittest.main()
