import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from tools.melonly_bench import _json_repository, main, run_benchmark


class MelonlyBenchmarkTests(unittest.TestCase):
    def test_small_synthetic_run_checks_result_parity_and_backend_safety(self):
        result = run_benchmark((250,), repeats=1, players=25)
        self.assertTrue(result["synthetic_data_only"])
        self.assertFalse(result["production_backend_changed"])
        self.assertEqual(len(result["results"]), 1)

        measured = result["results"][0]
        self.assertEqual(measured["entries"], 250)
        self.assertEqual(measured["parity"], "passed")
        self.assertGreater(measured["json_bytes"], 0)
        self.assertGreater(measured["sqlite_bytes"], 0)
        self.assertIn("player_history_by_name", measured["operations"])
        self.assertIn("search_payload_unique", measured["operations"])

    def test_json_repository_fixture_can_read_and_write_its_temp_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "logs.json"
            repo = _json_repository(path)
            self.assertEqual(repo.export_all(), [])
            repo.append({"id": "fixture", "type": "Warn"})
            self.assertEqual(repo.export_all(), [{"id": "fixture", "type": "Warn"}])
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))[0]["id"], "fixture")

    def test_cli_writes_a_machine_readable_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "benchmark.json"
            args = ["melonly_bench.py", "--entries", "30", "--repeats", "1",
                    "--players", "6", "--json", str(report)]
            output = StringIO()
            with patch("sys.argv", args), redirect_stdout(output):
                self.assertEqual(main(), 0)
            parsed = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(parsed["results"][0]["entries"], 30)
            self.assertIn('"production_backend_changed": false', output.getvalue())

    def test_benchmark_rejects_unsafe_or_unbounded_parameters(self):
        for entries, repeats, players in (
            ((), 1, 10),
            ((100_001,), 1, 10),
            ((100,), 0, 10),
            ((100,), 1, 0),
        ):
            with self.subTest(entries=entries, repeats=repeats, players=players):
                with self.assertRaises(ValueError):
                    run_benchmark(entries, repeats, players)


if __name__ == "__main__":
    unittest.main()
