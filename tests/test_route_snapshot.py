"""K-16: the set of active routes and their handlers matches docs/routen_snapshot.json.

Which implementation of a page is active depends on registration order and `remove_and_add`.
If this test fails after an intended change, regenerate with `python tools/route_map.py`
and review the diff of docs/routen_snapshot.json.
"""
import importlib.util
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
# Known until the clean-up in A-01: FastAPI serves the first of the two handlers.
KNOWN_DUPLICATES = {("GET", "/ultimate")}


def load_route_map():
    spec = importlib.util.spec_from_file_location("pulse_route_map", ROOT / "tools" / "route_map.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RouteSnapshotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import webserver
        import cogs.pulse_suite as suite
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        with patch.object(suite, "DB_PATH", str(Path(cls.tmp.name) / "pulse_suite.db")):
            suite.register_routes()
        cls.routes = load_route_map().effective_routes(webserver.app)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_every_method_and_path_is_registered_once(self):
        counts = Counter((r["method"], r["path"]) for r in self.routes)
        duplicates = {key for key, n in counts.items() if n > 1}
        self.assertEqual(duplicates - KNOWN_DUPLICATES, set())

    def test_active_handlers_match_snapshot(self):
        expected = set(json.loads((ROOT / "docs" / "routen_snapshot.json").read_text(encoding="utf-8")))
        actual = {f'{r["method"]} {r["path"]} -> {r["module"]}.{r["function"]}' for r in self.routes}
        self.assertEqual(sorted(actual - expected), [], "neue/geänderte Routen – Snapshot prüfen und neu erzeugen")
        self.assertEqual(sorted(expected - actual), [], "Routen fehlen oder werden von anderem Handler bedient")

    def test_no_public_api_docs(self):
        paths = {r["path"] for r in self.routes}
        self.assertFalse(paths & {"/docs", "/redoc", "/openapi.json"})


if __name__ == "__main__":
    unittest.main()
