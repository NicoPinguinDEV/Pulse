"""Syntax-check the remote deployment script without connecting to a server."""
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class DeployScriptTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("bash"), "bash is required for shell syntax validation")
    def test_remote_deploy_script_parses_without_execution(self):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "tools" / "deploy_remote.sh")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
