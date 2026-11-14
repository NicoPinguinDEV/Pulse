"""Die Zahlen im Funktionsaudit werden aus den 177 Einzelzeilen gezählt, nicht von Hand gepflegt (tools/audit_counts.py)."""
import importlib.util
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("audit_counts", ROOT / "tools" / "audit_counts.py")
audit_counts = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit_counts)

AUDIT = (ROOT / "docs" / "PULSE_FEATURE_AUDIT_177.md").read_text(encoding="utf-8")
PROGRESS = (ROOT / "docs" / "PULSE_177_IMPLEMENTATION_PROGRESS.md").read_text(encoding="utf-8")


class AuditCountTests(unittest.TestCase):
    def setUp(self):
        self.rows, self.areas = audit_counts.parse(AUDIT)
        self.totals = audit_counts.totals(self.rows)

    def test_exactly_the_177_functions(self):
        self.assertEqual(set(self.rows), set(range(1, 178)))
        self.assertEqual(sum(self.totals.values()), 177)

    def test_overview_tables_match_the_rows(self):
        self.assertEqual(audit_counts.rewrite(AUDIT), AUDIT, "python tools/audit_counts.py --write ausführen")

    def test_headline_states_the_counted_numbers(self):
        match = re.search(r"Stand mit #99: \*\*(\d+) fertig / (\d+) teilweise / (\d+) offen\*\*", AUDIT)
        self.assertIsNotNone(match)
        self.assertEqual(tuple(map(int, match.groups())), (self.totals["Fertig"], self.totals["Teilweise"], self.totals["Offen"]))

    def test_progress_table_matches_the_rows(self):
        match = re.search(r"\| Fertig \| (\d+) \((\d+) mit #99-Branch\) \|\n\| Teilweise \| (\d+) \((\d+) mit #99-Branch\) \|", PROGRESS)
        self.assertIsNotNone(match)
        main_done, branch_done, main_partial, branch_partial = map(int, match.groups())
        self.assertEqual((branch_done, branch_partial), (self.totals["Fertig"], self.totals["Teilweise"]))
        self.assertEqual((main_done + 1, main_partial - 1), (branch_done, branch_partial))   # #99 is the only difference to main

    def test_no_stale_merge_statements(self):
        for text in (AUDIT, PROGRESS):
            self.assertNotIn("noch nicht gemergt", text)
            self.assertNotIn("Noch nicht in main gemergt", text)
            self.assertNotIn("noch nicht in main gemergt", text)


if __name__ == "__main__":
    unittest.main()
