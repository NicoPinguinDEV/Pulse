"""Stored UTC timestamps keep the offset-free format of the former datetime.utcnow().isoformat()."""
import ast
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_helper():
    """Load utc_now_naive_iso from webserver.py without importing the whole dashboard."""
    source = (ROOT / "webserver.py").read_text(encoding="utf-8")
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == "utc_now_naive_iso")
    namespace = {"datetime": datetime, "timezone": timezone}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "webserver.py", "exec"), namespace)
    return namespace["utc_now_naive_iso"]


class UtcTimestampFormatTest(unittest.TestCase):
    def test_format_matches_legacy_utcnow(self):
        value = load_helper()()
        parsed = datetime.fromisoformat(value)
        self.assertIsNone(parsed.tzinfo)
        self.assertEqual(len(value), len("2026-01-01T00:00:00"))
        delta = abs((datetime.now(timezone.utc).replace(tzinfo=None) - parsed).total_seconds())
        self.assertLess(delta, 5)

    def test_no_deprecated_utcnow_in_runtime_code(self):
        offenders = [p.name for p in [*ROOT.glob("*.py"), *ROOT.glob("cogs/*.py")]
                     if "datetime.utcnow(" in p.read_text(encoding="utf-8")]
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
