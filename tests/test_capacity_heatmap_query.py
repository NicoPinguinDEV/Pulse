"""Regression tests for uncapped time-window reads used by the capacity heatmap."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pulse_db
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class CapacityHeatmapQueryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)
        pulse_db.init_db()

    def test_window_query_returns_all_ticket_and_voice_timestamps_without_list_caps(self):
        start = datetime(2026, 10, 1, tzinfo=timezone.utc)
        end = datetime(2026, 11, 1, tzinfo=timezone.utc)
        in_window = datetime(2026, 10, 20, 8, 30, tzinfo=timezone.utc).isoformat()
        out_window = datetime(2026, 9, 30, 23, 59, tzinfo=timezone.utc).isoformat()

        with pulse_db.connect() as connection:
            ticket_rows = [
                (
                    f"heatmap-{index}", f"heatmap-channel-{index}", "1", "2", "Test", "support",
                    "open", "normal", None, in_window,
                )
                for index in range(2005)
            ]
            ticket_rows.append((
                "outside-window", "outside-channel", "1", "2", "Test", "support",
                "open", "normal", None, out_window,
            ))
            connection.executemany(
                "INSERT INTO tickets(id,channel_id,guild_id,user_id,user_name,category,status,priority,claimed_by_id,opened_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                ticket_rows,
            )
            connection.executemany(
                "INSERT INTO voice_support_case_history(case_id,guild_id,user_id,user_name,created_at,support_started_at,closed_at) "
                "VALUES(?,?,?,?,?,?,?)",
                [
                    ("voice-created", "1", "2", "Test", in_window, None, end.isoformat()),
                    ("voice-started", "1", "2", "Test", None, in_window, end.isoformat()),
                    ("voice-closed", "1", "2", "Test", None, None, in_window),
                    ("voice-outside", "1", "2", "Test", out_window, None, end.isoformat()),
                ],
            )

        self.assertEqual(len(pulse_db.list_tickets(limit=5000)), 2000)
        tickets, voice = pulse_db.heatmap_inputs(start, end)
        self.assertEqual(len(tickets), 2005)
        self.assertEqual(tickets[0], {"opened_at": in_window})
        self.assertEqual(len(voice), 3)
        self.assertEqual(
            [row.get("created_at") or row.get("support_started_at") or row.get("closed_at") for row in voice],
            [in_window, in_window, in_window],
        )


if __name__ == "__main__":
    unittest.main()
