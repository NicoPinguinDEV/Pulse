import unittest

from tools.melonly_bench import run_benchmark


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
