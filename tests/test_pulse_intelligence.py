"""Unit tests for the local Pulse Intelligence tools (no Discord connection required)."""
import json
import sqlite3
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # README gate runs it as `python tests/test_pulse_intelligence.py`

from pulse_intelligence import (
    build_copilot_report,
    build_findings,
    database_workload_counts,
    memory_state,
    simulate_scenario,
    system_health,
    ticket_is_overdue,
    validate_workflow_json,
)


class PulseIntelligenceTests(unittest.TestCase):
    def test_memory_thresholds_match_shared_vps_policy(self):
        self.assertEqual(memory_state(499.9)["level"], 0)
        self.assertEqual(memory_state(500)["level"], 1)
        self.assertEqual(memory_state(620)["level"], 2)
        self.assertEqual(memory_state(700)["level"], 3)
        self.assertEqual(memory_state(None)["label"], "unavailable")

    def test_ticket_sla_uses_priority_and_ignores_closed_tickets(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        self.assertTrue(ticket_is_overdue({
            "status": "open", "priority": "urgent", "opened_at": "2026-10-17T11:00:00+00:00",
        }, now))
        self.assertFalse(ticket_is_overdue({
            "status": "open", "priority": "normal", "opened_at": "2026-10-17T11:00:00+00:00",
        }, now))
        self.assertFalse(ticket_is_overdue({
            "status": "closed", "priority": "urgent", "opened_at": "2026-10-17T10:00:00+00:00",
        }, now))

    def test_database_workload_counts_are_complete_and_preserve_sla_rules(self):
        connection = sqlite3.connect(":memory:")
        self.addCleanup(connection.close)
        connection.executescript(
            """
            CREATE TABLE tickets(status TEXT, priority TEXT, opened_at TEXT);
            CREATE TABLE tasks(status TEXT, due_at TEXT);
            """
        )
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        old = (now - timedelta(hours=25)).isoformat(timespec="seconds")
        with connection:
            connection.executemany(
                "INSERT INTO tickets(status,priority,opened_at) VALUES(?,?,?)",
                [
                    ("open", "normal", old) for _ in range(2005)
                ] + [
                    ("open", "urgent", (now - timedelta(minutes=31)).isoformat()),
                    ("open", "urgent", (now - timedelta(minutes=29)).isoformat()),
                    ("open", "high", (now - timedelta(minutes=241)).isoformat()),
                    ("open", "custom-priority", old),
                    ("open", "normal", "not-a-date"),
                    ("open", "normal", (now - timedelta(hours=25)).replace(tzinfo=None).isoformat()),
                    ("", "", old),
                    ("closed", "urgent", old),
                    ("resolved", "urgent", old),
                ],
            )
            connection.executemany(
                "INSERT INTO tasks(status,due_at) VALUES(?,?)",
                [("open", old) for _ in range(1003)] + [
                    ("done", old),
                    ("archived", old),
                    ("open", "not-a-date"),
                    ("open", (now + timedelta(days=1)).isoformat()),
                ],
            )

        metrics = database_workload_counts(connection, now)
        self.assertEqual(metrics["open_tickets"], 2012)
        self.assertEqual(metrics["urgent_tickets"], 2)
        self.assertEqual(metrics["ticket_sla_overdue"], 2010)
        self.assertEqual(metrics["overdue_tasks"], 1003)

    def test_system_health_never_masks_a_critical_dependency(self):
        result = system_health([{"state": "ok"}, {"state": "warning"}, {"state": "critical"}])
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["critical_count"], 1)

    def test_copilot_findings_are_grounded_in_supplied_metrics(self):
        metrics = {
            "open_tickets": 7, "urgent_tickets": 2, "ticket_sla_overdue": 1,
            "overdue_tasks": 3, "open_cases": 4, "automation_failures_24h": 0,
            "active_staff": 2, "rss_mb": 215,
        }
        report = build_copilot_report("Warum sind meine Tickets offen?", metrics)
        self.assertEqual(report["focus"], "tickets")
        self.assertEqual(report["mode"], "local_rules_no_external_ai")
        self.assertIn("7 offene Tickets", report["summary"])
        self.assertTrue(any(item["topic"] == "tickets" for item in report["recommendations"]))
        self.assertTrue(any(item["topic"] == "tasks" for item in build_findings(metrics)))

    def test_simulations_are_bounded_and_read_only(self):
        metrics = {"open_tickets": 10, "active_staff": 4, "rss_mb": 695}
        surge = simulate_scenario("ticket_surge", 5, metrics)
        self.assertEqual(surge["projected"], 15)
        absence = simulate_scenario("staff_absence", 100, metrics)
        self.assertEqual(absence["projected"], 0)
        self.assertEqual(absence["status"], "critical")
        ram = simulate_scenario("ram_spike", 10, metrics)
        self.assertEqual(ram["projected"], 705)
        self.assertEqual(ram["status"], "critical")
        self.assertEqual(metrics["open_tickets"], 10)
        with self.assertRaises(ValueError):
            simulate_scenario("execute_discord_kick", 1, metrics)

    def test_workflow_sandbox_accepts_safe_draft_and_rejects_arbitrary_actions(self):
        safe = {
            "schema_version": 1,
            "name": "SLA reminder",
            "trigger": "ticket_overdue",
            "conditions": [{"field": "priority", "operator": "in", "value": ["high", "urgent"]}],
            "actions": [{"type": "notify_assignee", "message": "Please review the ticket"}],
            "enabled": False,
        }
        ok, errors, value = validate_workflow_json(json.dumps(safe))
        self.assertTrue(ok, errors)
        self.assertEqual(value["name"], "SLA reminder")
        unsafe = dict(safe)
        unsafe["actions"] = [{"type": "execute_shell", "message": "bad"}]
        ok, errors, value = validate_workflow_json(json.dumps(unsafe))
        self.assertFalse(ok)
        self.assertIsNone(value)
        self.assertTrue(any("type wird nicht unterstützt" in error for error in errors))
        ok, errors, _ = validate_workflow_json("{invalid")
        self.assertFalse(ok)
        self.assertIn("kein gültiges JSON", errors[0])


if __name__ == "__main__":
    unittest.main()
