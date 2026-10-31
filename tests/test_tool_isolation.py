"""Phase 8: dev tools that start Pulse with fake data must keep every runtime file in their temp folder.

webserver.py runs os.chdir(BASE_DIR) on import. The preview server (E2E) and the load test changed into their temp
folder *before* importing it, so relative runtime paths – backups/ above all – were written into the checkout,
which on a server holds the real data.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ToolIsolationTests(unittest.TestCase):
    def test_tools_return_to_temp_folder_after_importing_webserver(self):
        for name in ("preview_server.py", "load_test.py"):
            with self.subTest(tool=name):
                source = (ROOT / "tools" / name).read_text(encoding="utf-8")
                imported = source.index("import webserver as ws")
                chdirs = [m.start() for m in re.finditer(r"os\.chdir\(tmp\)", source)]
                self.assertTrue(any(pos > imported for pos in chdirs), "os.chdir(tmp) fehlt nach dem Import von webserver")
                self.assertRegex(source[imported:], r"ws\.BACKUP_DIR\s*=\s*str\(Path\(tmp\)")


if __name__ == "__main__":
    unittest.main()
