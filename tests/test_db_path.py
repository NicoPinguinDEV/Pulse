"""D-05: every module uses one database file, including a custom PULSE_DB_PATH."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_ultimate

ROOT = Path(__file__).resolve().parents[1]


class DbPathTests(unittest.TestCase):
    def test_ultimate_follows_pulse_db_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "eigene.db")
            with patch.object(pulse_db, "DB_PATH", target):
                self.assertEqual(pulse_ultimate.ultimate_db(), Path(os.path.abspath(target)))

    def test_env_variable_applies_to_all_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = str(Path(tmp) / "env.db")
            code = ("import os, pulse_db, pulse_ultimate;"
                    "print(os.path.abspath(pulse_db.DB_PATH)==str(pulse_ultimate.ultimate_db()))")
            env = dict(os.environ, PULSE_DB_PATH=target, PYTHONPATH=str(ROOT))
            out = subprocess.run([sys.executable, "-c", code], cwd=tmp, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(out.stdout.strip().splitlines()[-1], "True", out.stderr[-2000:])

    def test_no_module_connects_to_a_fixed_pulse_db(self):
        for rel in ("pulse_ultimate.py", "pulse_next.py"):
            source = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("connect(ULTIMATE_DB", source)
            self.assertNotIn("connect(u.ULTIMATE_DB", source)


if __name__ == "__main__":
    unittest.main()
