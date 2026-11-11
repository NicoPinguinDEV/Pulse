"""Automatic ticket assignment skips team members with an active Abmeldung."""
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pulse_ticket_distribution import active_absence_ids
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (no ./pulse.db)

TODAY = date(2026, 11, 11)


class ActiveAbsenceTests(unittest.TestCase):
    def test_absence_counts_on_its_first_and_last_day(self):
        rows = [
            (10, "11.11.2026", "20.11.2026", 1),   # starts today
            (11, "01.11.2026", "11.11.2026", 1),   # ends today
            (12, "01.11.2026", "10.11.2026", 1),   # ended yesterday
            (13, "12.11.2026", "20.11.2026", 1),   # starts tomorrow
        ]
        self.assertEqual(active_absence_ids(rows, 1, TODAY), {"10", "11"})

    def test_both_stored_date_formats_are_understood(self):
        rows = [(10, "2026-11-01", "2026-11-30", 1), (11, "2026-10-01", "2026-10-31", 1)]
        self.assertEqual(active_absence_ids(rows, 1, TODAY), {"10"})

    def test_unreadable_dates_count_as_absent(self):
        rows = [(10, "", "irgendwann", 1), (11, None, None, 1), (12, "", "10.11.2026", 1)]
        self.assertEqual(active_absence_ids(rows, 1, TODAY), {"10", "11"})

    def test_other_guilds_are_ignored_and_legacy_rows_apply_everywhere(self):
        rows = [(10, "01.11.2026", "30.11.2026", 2), (11, "01.11.2026", "30.11.2026", None)]
        self.assertEqual(active_absence_ids(rows, 1, TODAY), {"11"})
        self.assertEqual(active_absence_ids(rows, 2, TODAY), {"10", "11"})

    def test_broken_rows_are_skipped(self):
        rows = [("x", "01.11.2026", "30.11.2026", 1), (None, "", "", 1), (10,), None]
        self.assertEqual(active_absence_ids(rows, 1, TODAY), set())
        self.assertEqual(active_absence_ids(None, 1, TODAY), set())


class AssignmentSkipsAbsentStaffTests(unittest.TestCase):
    def setUp(self):
        import cogs.tickets as tickets
        import webserver as ws
        self.tickets = tickets
        self.tmp = tempfile.TemporaryDirectory()
        self.db_file = str(Path(self.tmp.name) / "abmeldungen.db")
        role = SimpleNamespace(id=40)
        self.free = SimpleNamespace(id=20, display_name="Frei", name="frei", bot=False, roles=[role])
        self.busy = SimpleNamespace(id=10, display_name="Voll", name="voll", bot=False, roles=[role])
        members = {m.id: m for m in (self.free, self.busy)}
        self.guild = SimpleNamespace(id=1, members=list(members.values()), get_member=members.get)
        open_tickets = [{"guild_id": "1", "status": "open", "claimed_by_id": "10", "priority": "normal"}]
        self.patches = [
            patch.object(tickets, "ticket_config", return_value={
                "distribution": {"enabled": True, "max_open_per_staff": 5}}),
            patch.object(tickets, "category_roles", return_value=[role]),
            patch.object(tickets, "list_tickets", return_value=open_tickets),
            patch.object(tickets.pulse_paths, "data_file", return_value=self.db_file),
            patch.object(ws, "load_config", return_value={}),
            patch.object(ws, "compute_perms", return_value=({"can_manage_tickets": True}, {})),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.tmp.cleanup()

    def write_absences(self, rows):
        conn = sqlite3.connect(self.db_file)
        try:
            conn.execute("CREATE TABLE abmeldungen (user_id INTEGER PRIMARY KEY, user_name TEXT NOT NULL, "
                         "grund TEXT NOT NULL, von TEXT NOT NULL, bis TEXT NOT NULL, original_nick TEXT, guild_id INTEGER)")
            conn.executemany("INSERT INTO abmeldungen VALUES (?,?,?,?,?,?,?)", rows)
            conn.commit()
        finally:
            conn.close()

    def choose(self):
        return self.tickets.choose_ticket_auto_assignee(self.guild, "support")

    def test_without_absence_database_the_least_loaded_member_is_chosen(self):
        self.assertIs(self.choose(), self.free)

    def test_absent_member_is_skipped_even_when_least_loaded(self):
        self.write_absences([(20, "Frei", "Urlaub", "01.01.2020", "31.12.2099", None, 1)])
        self.assertIs(self.choose(), self.busy)

    def test_expired_absence_does_not_block_assignment(self):
        self.write_absences([(20, "Frei", "Urlaub", "01.01.2020", "31.12.2020", None, 1)])
        self.assertIs(self.choose(), self.free)

    def test_ticket_stays_unassigned_when_everyone_is_absent(self):
        self.write_absences([
            (20, "Frei", "Urlaub", "01.01.2020", "31.12.2099", None, 1),
            (10, "Voll", "Krank", "01.01.2020", "31.12.2099", None, 1),
        ])
        self.assertIsNone(self.choose())

    def test_absence_on_another_guild_is_ignored(self):
        self.write_absences([(20, "Frei", "Urlaub", "01.01.2020", "31.12.2099", None, 2)])
        self.assertIs(self.choose(), self.free)

    def test_unreadable_database_falls_back_to_assignment_without_absences(self):
        Path(self.db_file).write_bytes(b"das ist keine sqlite-datei, sondern muell" * 20)
        self.assertIs(self.choose(), self.free)

    def test_reading_absences_never_writes_to_the_database(self):
        self.write_absences([(20, "Frei", "Urlaub", "01.01.2020", "31.12.2099", None, 1)])
        before = Path(self.db_file).read_bytes()
        self.choose()
        self.assertEqual(Path(self.db_file).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
