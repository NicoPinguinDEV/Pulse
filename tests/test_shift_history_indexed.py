"""Phase 14.8: indexed shift-history reads and SQLite aggregates."""
import unittest
from datetime import timedelta
from unittest.mock import patch

import pulse_shift_history as store
import webserver as ws
from pulse_shift_utils import credited_shift_seconds
from tests.test_shift_history_sqlite import ShiftHistoryBase, synthetic_history


class IndexedReadTests(ShiftHistoryBase):
    def setUp(self):
        super().setUp()
        self.history = synthetic_history(1200, members=12, seed=148)
        self.history.extend([
            {"mod_id": "1000", "date": "2026-10-03", "duration_seconds": 321,
             "shift_event_id": "duplicate", "unknown_payload": {"keep": [1, 2]}},
            {"mod_id": "1000", "date": "2026-10-03", "duration_seconds": 321,
             "shift_event_id": "duplicate", "unknown_payload": {"keep": [1, 2]}},
            {"mod_id": "1001", "date": "2026-10-04", "duration_seconds": 654,
             "shift_event_id": "range-end"},
        ])
        self.seed(self.history, {"1000": {"status": "online", "date": "2026-10-01",
                                           "started_at_iso": (ws.now_de() - timedelta(hours=1)).isoformat(),
                                           "segment_start_iso": ws.now_de().isoformat(),
                                           "accumulated_seconds": 0}})
        self.json_member_page = store.query_history(ws, mod_id="1000", date_from="2026-10-03",
                                                    date_to="2026-10-05", limit=17)

    def migrate(self):
        self.assertEqual(store.migrate(ws)["status"], "migrated")

    def test_json_and_sqlite_member_date_page_are_identical(self):
        self.migrate()
        sqlite_page = store.query_history(ws, mod_id="1000", date_from="2026-10-03",
                                          date_to="2026-10-05", limit=17)
        self.assertEqual(sqlite_page, self.json_member_page)

    def test_member_filter_and_stable_keyset_pagination_keep_duplicates(self):
        self.migrate()
        expected = [row for row in self.history if str(row.get("mod_id")) == "1000"]
        found, cursor = [], None
        while True:
            page = store.query_history(ws, mod_id="1000", limit=23, after_seq=cursor)
            found.extend(page["items"])
            if not page["has_more"]:
                break
            cursor = page["next_cursor"]
            self.assertIsInstance(cursor, int)
        self.assertEqual(found, expected)
        self.assertEqual(found[-2]["unknown_payload"], {"keep": [1, 2]})
        self.assertEqual(found[-2], found[-1])

    def test_date_bounds_are_inclusive_start_and_exclusive_end(self):
        self.migrate()
        result = store.query_history(ws, date_from="2026-10-03", date_to="2026-10-05", limit=1000)
        expected = [entry for entry in self.history
                    if isinstance(entry.get("date"), str) and "2026-10-03" <= entry["date"] < "2026-10-05"]
        self.assertEqual(result["items"], expected)

    def test_pagination_rejects_unsafe_or_ambiguous_limits(self):
        for value in (0, -1, 1001, True, "10"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                store.query_history(ws, limit=value)
        with self.assertRaises(ValueError):
            store.query_history(ws, after_seq=-1)
        with self.assertRaises(ValueError):
            store.query_history(ws, date_from="2026-2-1")
        with self.assertRaises(ValueError):
            store.query_history(ws, date_from="2026-10-05", date_to="2026-10-05")

    def test_member_query_uses_existing_composite_index(self):
        self.migrate()
        plan = " ".join(store.explain_member_query_plan(ws, "1000", "2026-10-01"))
        self.assertIn("idx_shift_history_member", plan)

    def test_member_view_does_not_materialize_other_members(self):
        self.migrate()
        expected = [row for row in self.history if str(row.get("mod_id")) == "1000"]
        with patch.object(store, "_payloads", side_effect=AssertionError("full history read attempted")):
            view = ws.load_member_shifts("1000")
            state = ws.load_shift_state()
        self.assertEqual(view["history"], expected)
        self.assertIn("1000", view["active_shifts"])
        self.assertNotIn("history", state)
        self.assertIn("1000", state["active_shifts"])

    def test_expired_personal_shift_is_only_projected_not_persisted(self):
        old = (ws.now_de() - timedelta(hours=30)).isoformat()
        active = {"1000": {"status": "online", "started_at_iso": old, "segment_start_iso": old,
                           "date": old[:10], "accumulated_seconds": 0}}
        self.seed(self.history, active)
        self.migrate()
        before = self.db_rows()
        view = ws.load_member_shifts("1000")
        self.assertNotIn("1000", view["active_shifts"])
        self.assertTrue(view["history"][-1]["auto_closed"])
        self.assertEqual(self.db_rows(), before)

    def test_sqlite_weekly_and_lifetime_aggregates_match_python_reference(self):
        expected_weekly = ws.weekly_history_totals(self.history)
        expected_total = sum(credited_shift_seconds(entry) for entry in self.history)
        self.migrate()
        self.assertEqual(store.weekly_totals(ws), expected_weekly)
        self.assertEqual(store.total_credited_seconds(ws), expected_total)
        member_ids = ["1000", "1001"]
        self.assertEqual(store.weekly_totals(ws, member_ids),
                         {uid: value for uid, value in expected_weekly.items() if uid in member_ids})

    def test_empty_history_and_empty_member_selection(self):
        self.seed([])
        self.migrate()
        self.assertEqual(store.query_history(ws), {"items": [], "total": 0, "next_cursor": None, "has_more": False})
        self.assertEqual(store.weekly_totals(ws), {})
        self.assertEqual(store.weekly_totals(ws, []), {})
        self.assertEqual(store.total_credited_seconds(ws), 0)

    def test_parameter_values_are_data_not_sql(self):
        self.migrate()
        result = store.query_history(ws, mod_id="1000' OR 1=1 --", limit=10)
        self.assertEqual(result["items"], [])
        self.assertEqual(result["total"], 0)


    def test_store_backed_member_weekly_seconds_matches_existing_calculation(self):
        active = {"1000": {"date": ws.now_de().strftime("%Y-%m-%d"), "started_at_iso": ws.now_de().isoformat(),
                           "segment_start_iso": ws.now_de().isoformat(), "accumulated_seconds": 0}}
        expected = ws.calculate_weekly_seconds("1000", self.history, active)
        self.migrate()
        state = ws.load_shift_state()
        actual = ws.calculate_weekly_seconds_for_member("1000", state["active_shifts"])
        self.assertEqual(actual, expected)

    def test_json_member_view_remains_the_legacy_full_view(self):
        view = ws.load_member_shifts("1000")
        self.assertEqual(view["history"], self.history)
        self.assertEqual(view["active_shifts"]["1000"]["status"], "online")

    def test_json_aggregates_match_the_legacy_calculations(self):
        expected_weekly = ws.weekly_history_totals(self.history)
        expected_total = sum(credited_shift_seconds(entry) for entry in self.history)
        self.assertEqual(store.weekly_totals(ws), expected_weekly)
        self.assertEqual(store.total_credited_seconds(ws), expected_total)


    def test_cockpit_date_aggregates_match_legacy_helper_with_old_dates(self):
        from pulse_cockpit_utils import last_shift_dates, seconds_per_member

        today = ws.now_de().date()
        this_start = today - timedelta(days=today.weekday())
        this_end = today
        previous_start = this_start - timedelta(days=7)
        previous_end = today - timedelta(days=7)
        old_this = f"{this_start.year}-{this_start.month}-{this_start.day}"
        old_previous = f"{previous_start.year}-{previous_start.month}-{previous_start.day}"
        legacy_rows = [
            {"mod_id": "1000", "date": old_this, "duration_seconds": 125},
            {"mod_id": "1000", "date": this_start.isoformat(), "duration_seconds": 300},
            {"mod_id": "1001", "date": old_previous, "duration_seconds": 500},
            {"mod_id": "1001", "date": "20.10.2026", "duration_seconds": 99},
            {"mod_id": "1001", "date": "2026-13-01", "duration_seconds": 99},
        ]
        self.seed(legacy_rows)
        member_ids = ["1000", "1001"]
        expected_this = seconds_per_member(legacy_rows, this_start, this_end)
        expected_prev = seconds_per_member(legacy_rows, previous_start, previous_end)
        expected_latest = last_shift_dates(legacy_rows)
        self.assertEqual(store.range_totals(
            ws, member_ids, this_start.isoformat(), (this_end + timedelta(days=1)).isoformat()
        ), expected_this)
        self.assertEqual(store.range_totals(
            ws, member_ids, previous_start.isoformat(), (previous_end + timedelta(days=1)).isoformat()
        ), expected_prev)
        self.assertEqual(store.latest_shift_dates(ws, member_ids), expected_latest)
        self.migrate()
        self.assertEqual(store.range_totals(
            ws, member_ids, this_start.isoformat(), (this_end + timedelta(days=1)).isoformat()
        ), expected_this)
        self.assertEqual(store.range_totals(
            ws, member_ids, previous_start.isoformat(), (previous_end + timedelta(days=1)).isoformat()
        ), expected_prev)
        self.assertEqual(store.latest_shift_dates(ws, member_ids), expected_latest)

    def test_per_member_lifetime_totals_match_legacy_filter(self):
        uid = "1000"
        expected = sum(
            credited_shift_seconds(entry) for entry in self.history
            if isinstance(entry, dict) and str(entry.get("mod_id")) == uid
        )
        self.migrate()
        self.assertEqual(store.total_credited_seconds(ws, mod_id=uid), expected)

    unittest.main()
