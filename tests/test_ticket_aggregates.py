"""Regression tests for unbounded ticket aggregates used by Pulse dashboards."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pulse_db
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class TicketAggregateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def _insert_ticket(self, connection, ticket_id, *, status="open", priority="normal",
                       opened_at="2026-11-18T12:00:00+00:00", claimed_by_id=None):
        connection.execute(
            "INSERT INTO tickets(id,channel_id,guild_id,user_id,user_name,category,status,priority,claimed_by_id,opened_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?)",
            (ticket_id, f"channel-{ticket_id}", "1", "2", "Test", "support",
             status, priority, claimed_by_id, opened_at),
        )

    def test_open_ticket_channel_refs_include_rows_beyond_list_limit(self):
        with pulse_db.connect() as connection:
            rows = []
            for index in range(2005):
                rows.append((
                    f"orphan-probe-{index}", f"probe-channel-{index}", "1", "2", "Test", "support",
                    "open", "normal", None, "2026-11-18T12:00:00+00:00",
                ))
            connection.executemany(
                "INSERT INTO tickets(id,channel_id,guild_id,user_id,user_name,category,status,priority,claimed_by_id,opened_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                rows,
            )

        self.assertEqual(len(pulse_db.list_tickets(limit=5000)), 2000)
        refs = pulse_db.list_open_ticket_channel_refs()
        self.assertEqual(len(refs), 2005)
        self.assertIn(("orphan-probe-2004", "probe-channel-2004"), refs)


    def test_support_sla_counts_and_detail_page_data_match_existing_thresholds(self):
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        with pulse_db.connect() as connection:
            self._insert_ticket(connection, "urgent-old", priority="urgent",
                                opened_at=(now - timedelta(minutes=31)).isoformat(), claimed_by_id="9")
            self._insert_ticket(connection, "high-old", priority="high",
                                opened_at=(now - timedelta(minutes=241)).isoformat(), claimed_by_id="9")
            self._insert_ticket(connection, "unknown-old", priority="custom",
                                opened_at=(now - timedelta(hours=25)).isoformat(), claimed_by_id="9")
            self._insert_ticket(connection, "urgent-fresh", priority="urgent",
                                opened_at=(now - timedelta(minutes=29)).isoformat(), claimed_by_id="9")
            self._insert_ticket(connection, "high-fresh", priority="high",
                                opened_at=(now - timedelta(minutes=239)).isoformat(), claimed_by_id="9")
            self._insert_ticket(connection, "normal-fresh",
                                opened_at=(now - timedelta(hours=23, minutes=59)).isoformat(), claimed_by_id="9")
            self._insert_ticket(connection, "urgent-closed", status="closed", priority="urgent",
                                opened_at=(now - timedelta(days=2)).isoformat(), claimed_by_id="9")
            self._insert_ticket(connection, "invalid-date", opened_at="not-a-date", claimed_by_id="9")
            self._insert_ticket(connection, "naive-date", opened_at="2026-11-18T12:00:00", claimed_by_id="9")

        valid_open, overdue = pulse_db.sla_ticket_counts(now)
        self.assertEqual((valid_open, overdue), (6, 3))
        self.assertEqual(pulse_db.count_sla_overdue_tickets(now), 3)
        self.assertEqual(pulse_db.count_tickets(), 9)
        details = pulse_db.list_sla_overdue_tickets(now, limit=10)
        self.assertEqual({ticket["id"] for ticket, _age, _sla in details},
                         {"urgent-old", "high-old", "unknown-old"})
        self.assertEqual(
            {ticket["id"]: round(age) for ticket, age, _sla in details},
            {"unknown-old": 1500, "high-old": 241, "urgent-old": 31},
        )
        self.assertTrue(all(age >= sla for _ticket, age, sla in details))
        _total, closed_by_assignee = pulse_db.ticket_totals_by_assignee()
        self.assertEqual(closed_by_assignee["9"], 1)


    def test_aggregates_count_all_rows_beyond_the_bounded_list_limit(self):
        now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        old = (now - timedelta(days=2)).isoformat(timespec="seconds")
        with pulse_db.connect() as connection:
            rows = []
            for index in range(2005):
                rows.append((
                    f"bulk-{index}", f"bulk-channel-{index}", "1", "2", "Test", "support",
                    "closed" if index == 2004 else "open", "normal", "7", old,
                ))
            connection.executemany(
                "INSERT INTO tickets(id,channel_id,guild_id,user_id,user_name,category,status,priority,claimed_by_id,opened_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?)",
                rows,
            )
            self._insert_ticket(connection, "urgent-old", priority="urgent",
                                opened_at=(now - timedelta(minutes=31)).isoformat(), claimed_by_id=None)
            self._insert_ticket(connection, "high-old", priority="high",
                                opened_at=(now - timedelta(minutes=241)).isoformat(), claimed_by_id=None)
            self._insert_ticket(connection, "unknown-old", priority="custom",
                                opened_at=(now - timedelta(hours=25)).isoformat(), claimed_by_id=None)
            self._insert_ticket(connection, "urgent-fresh", priority="urgent",
                                opened_at=(now - timedelta(minutes=29)).isoformat(), claimed_by_id=None)
            self._insert_ticket(connection, "high-fresh", priority="high",
                                opened_at=(now - timedelta(minutes=239)).isoformat(), claimed_by_id=None)
            self._insert_ticket(connection, "normal-fresh",
                                opened_at=(now - timedelta(hours=23, minutes=59)).isoformat(), claimed_by_id=None)
            self._insert_ticket(connection, "urgent-closed", status="closed", priority="urgent",
                                opened_at=old, claimed_by_id=None)
            self._insert_ticket(connection, "invalid-date", opened_at="not-a-date", claimed_by_id=None)
            self._insert_ticket(connection, "naive-date", opened_at="2026-11-18T12:00:00", claimed_by_id=None)

        # list_tickets intentionally returns at most 2,000 rows; aggregate helpers must not inherit that cap.
        self.assertEqual(len(pulse_db.list_tickets(limit=5000)), 2000)
        self.assertEqual(pulse_db.count_open_tickets(), 2012)
        self.assertEqual(pulse_db.count_sla_overdue_tickets(now), 2007)

        claimed, closed = pulse_db.ticket_totals_by_assignee()
        self.assertEqual(claimed["7"], 2005)
        self.assertEqual(closed["7"], 1)
        self.assertEqual(claimed["None"], 9)
        self.assertEqual(closed["None"], 1)


if __name__ == "__main__":
    unittest.main()
