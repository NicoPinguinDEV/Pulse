"""Phase 21: local ticket intake trends are read-only and explainable."""
import sqlite3
import unittest
from datetime import datetime, timezone

from pulse_intelligence import build_copilot_report, build_findings, ticket_intake_counts


class TicketIntakeTrendTests(unittest.TestCase):
    def test_counts_use_two_non_overlapping_seven_day_windows(self):
        now = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc)
        connection = sqlite3.connect(":memory:")
        connection.execute("CREATE TABLE tickets(opened_at TEXT)")
        connection.executemany(
            "INSERT INTO tickets(opened_at) VALUES (?)",
            [
                ("2026-11-10T10:00:00+00:00",),
                ("2026-11-07T10:00:00+00:00",),
                ("2026-11-04T10:00:00+00:00",),
                ("2026-11-03T11:59:59+00:00",),
                ("2026-11-01T10:00:00+00:00",),
                ("2026-10-29T10:00:00+00:00",),
                ("2026-10-27T12:00:00+00:00",),
                ("2026-10-26T10:00:00+00:00",),
            ],
        )
        self.assertEqual(
            ticket_intake_counts(connection, now),
            {"tickets_opened_7d": 3, "tickets_opened_prev_7d": 4},
        )
        connection.close()

    def test_large_ticket_intake_increase_creates_a_grounded_finding(self):
        findings = build_findings({"tickets_opened_7d": 12, "tickets_opened_prev_7d": 6})
        alert = next(item for item in findings if item["title"].startswith("Ticketzugang gestiegen"))
        self.assertEqual(alert["topic"], "tickets")
        self.assertIn("vorherigen 7-Tage-Fenster waren es 6", alert["why"])

    def test_small_or_non_increasing_samples_do_not_create_an_alert(self):
        for current, previous in ((4, 1), (8, 7), (3, 0), (0, 0), (5, 5)):
            with self.subTest(current=current, previous=previous):
                findings = build_findings({
                    "tickets_opened_7d": current,
                    "tickets_opened_prev_7d": previous,
                })
                self.assertFalse(any(item["title"].startswith("Ticketzugang gestiegen") for item in findings))

    def test_ticket_copilot_explains_the_comparison_when_metrics_are_available(self):
        report = build_copilot_report("Wie entwickelt sich der Support?", {
            "open_tickets": 9,
            "urgent_tickets": 1,
            "ticket_sla_overdue": 2,
            "tickets_opened_7d": 12,
            "tickets_opened_prev_7d": 6,
        })
        self.assertIn("12 Tickets dazu", report["summary"])
        self.assertIn("vorherigen 7-Tage-Fenster waren es 6", report["summary"])
        self.assertEqual(report["mode"], "local_rules_no_external_ai")


if __name__ == "__main__":
    unittest.main()
