"""K-16: the set of active routes and their handlers matches docs/routen_snapshot.json.

Which implementation of a page is active depends on registration order and `remove_and_add`.
If this test fails after an intended change, regenerate with `python tools/route_map.py`
and review the diff of docs/routen_snapshot.json.
"""
import ast
import importlib.util
import inspect
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI

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


    def test_remove_and_add_preserves_other_methods_on_the_same_path(self):
        from pulse_pro import remove_and_add

        app = FastAPI()

        async def existing_handler():
            return {"handler": "existing"}

        async def replacement_handler():
            return {"handler": "replacement"}

        app.api_route("/shared", methods=["GET", "POST", "PUT"])(existing_handler)
        remove_and_add(app, "/shared", {"GET"}, replacement_handler)

        endpoints = {}
        for route in app.router.routes:
            if getattr(route, "path", None) == "/shared":
                for method in getattr(route, "methods", None) or ():
                    endpoints[method] = route.endpoint

        self.assertIs(endpoints.get("GET"), replacement_handler)
        self.assertIs(endpoints.get("POST"), existing_handler)
        self.assertIs(endpoints.get("PUT"), existing_handler)

    def test_remove_and_add_rejects_unsupported_methods_without_mutating_routes(self):
        from pulse_pro import remove_and_add

        app = FastAPI()

        async def existing_handler():
            return {"handler": "existing"}

        async def replacement_handler():
            return {"handler": "replacement"}

        app.get("/unchanged")(existing_handler)
        original_routes = list(app.router.routes)

        with self.assertRaisesRegex(ValueError, "unsupported route replacement method"):
            remove_and_add(app, "/unchanged", {"PUT"}, replacement_handler)

        self.assertEqual(app.router.routes, original_routes)

    def test_uncontested_routes_use_direct_fastapi_registration(self):
        import pulse_pro

        expected = {
            "/pulse-inbox/read/{nid}": "post",
            "/pulse-inbox/settings": "post",
            "/pulse-inbox/server-quiet": "post",
            "/voice-support": "get",
            "/voice-support/save": "post",
            "/ticket/{ticket_id}": "get",
            "/export/team.csv": "get",
            "/shift/admin-end": "post",
            "/shift/history/confirm-duration": "post",
            "/api/team/discord-status": "get",
            "/robots.txt": "get",
        }
        tree = ast.parse(inspect.getsource(pulse_pro.register))
        direct = {}
        replaced = set()

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            function = node.func
            if isinstance(function, ast.Name) and function.id == "remove_and_add":
                if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                    replaced.add(node.args[1].value)
            elif (
                isinstance(function, ast.Attribute)
                and isinstance(function.value, ast.Name)
                and function.value.id == "app"
                and function.attr in {"get", "post"}
                and node.args
                and isinstance(node.args[0], ast.Constant)
            ):
                path = node.args[0].value
                if path in expected:
                    direct.setdefault(path, []).append(function.attr)

        for path, method in expected.items():
            with self.subTest(path=path):
                self.assertEqual(direct.get(path), [method])
                self.assertNotIn(path, replaced)

    def test_no_public_api_docs(self):
        paths = {r["path"] for r in self.routes}
        self.assertFalse(paths & {"/docs", "/redoc", "/openapi.json"})


if __name__ == "__main__":
    unittest.main()
