"""Audit #71/#73/#75/#76/#81/#87/#88/#89/#92/#94: team-planning analytics (pure functions on synthetic data)."""
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_insights
import pulse_teamakte as ta

UTC = timezone.utc
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=UTC)  # a Sunday


def shift(uid, start, hours, **extra):
    row = {"mod_id": uid, "mod_name": f"M{uid}", "started_at_iso": start.isoformat(), "duration_seconds": int(hours * 3600)}
    row.update(extra)
    return row


def at(days_ago, hour=10):
    return (NOW - timedelta(days=days_ago)).replace(hour=hour, minute=0)


class WorkloadCase(unittest.TestCase):
    def test_points_levels_and_overdue(self):
        tasks = [{"assignee_id": "1", "status": "open", "priority": "urgent", "due_at": (NOW - timedelta(days=1)).isoformat()},
                 {"assignee_id": "1", "status": "open", "priority": "high"},
                 {"assignee_id": "1", "status": "done", "priority": "urgent"},
                 {"assignee_id": "9", "status": "open"}]  # not a team member
        tickets = [{"claimed_by_id": "1", "status": "open"}] * 3 + [{"claimed_by_id": "2", "status": "closed"}]
        rows = {r["id"]: r for r in ta.workload([("1", "Ann"), ("2", "Bo")], tasks, tickets, NOW)}
        self.assertEqual(rows["1"]["tasks"], 2)
        self.assertEqual(rows["1"]["overdue"], 1)
        self.assertEqual(rows["1"]["points"], 3 + 1 + 2 + 4.5)
        self.assertEqual(rows["1"]["level"], "überlastet")
        self.assertEqual((rows["2"]["points"], rows["2"]["level"]), (0.0, "ok"))
        self.assertEqual(ta.workload([("1", "Ann")], tasks, tickets, NOW)[0]["id"], "1")

    def test_garbage_input(self):
        self.assertEqual(ta.workload([], None, None), [])
        self.assertEqual(ta.workload([("1", "A")], [{"assignee_id": "1", "due_at": "kaputt"}], [{}])[0]["tasks"], 1)


class AnomalyCase(unittest.TestCase):
    def base(self):
        return [shift("1", at(d), 2) for d in range(1, 8)]

    def test_outlier_and_limit_with_reasons(self):
        hist = self.base() + [shift("1", at(10), 7), shift("1", at(11), 14)]
        rules = {(r["rule"], r["hours"]) for r in ta.shift_anomalies(hist, NOW)}
        self.assertIn(("outlier", 7.0), rules)
        self.assertIn(("limit", 14.0), rules)

    def test_too_few_samples_and_normal_variation_are_not_flagged(self):
        self.assertEqual(ta.shift_anomalies([shift("1", at(1), 2), shift("1", at(2), 6)], NOW), [])
        self.assertEqual(ta.shift_anomalies(self.base() + [shift("1", at(9), 3)], NOW), [])

    def test_short_many_and_unconfirmed(self):
        hist = [shift("2", at(1, 8), 0.05)] + [shift("3", at(2, h), 1) for h in (8, 10, 12, 14)]
        hist.append(shift("4", at(3), 0, auto_closed=True, measured_seconds=9999))
        rules = sorted(r["rule"] for r in ta.shift_anomalies(hist, NOW))
        self.assertEqual(rules.count("short"), 1)
        self.assertEqual(rules.count("many"), 1)
        self.assertEqual(rules.count("unconfirmed"), 1)

    def test_window_and_bad_rows(self):
        hist = [shift("1", at(200), 20), {"mod_id": "1", "started_at_iso": "kaputt"}, "x", None]
        self.assertEqual(ta.shift_anomalies(hist, NOW, days=90), [])


class AvailabilityCase(unittest.TestCase):
    def test_matrix_counts_people_per_weekday_hour(self):
        # NOW is Sunday 2026-11-15; Mondays 10:00 of the last two weeks, 2 h long
        monday = NOW - timedelta(days=6)
        hist = [shift("1", monday.replace(hour=10, minute=0), 2), shift("1", (monday - timedelta(days=7)).replace(hour=10, minute=0), 2),
                shift("2", monday.replace(hour=10, minute=0), 1)]
        m = ta.availability_matrix(hist, NOW, weeks=2)
        self.assertEqual(m["cells"][0][10], round((2 + 1) / 2, 2))
        self.assertEqual(m["cells"][0][11], 1.0 / 2 * 1.0 if False else round(2 / 2, 2))
        self.assertEqual(m["people"]["1"][0][10], 1.0)
        self.assertEqual(m["people"]["2"][0][10], 0.5)
        self.assertEqual(m["cells"][3][10], 0.0)

    def test_zero_credit_and_old_shifts_are_ignored(self):
        hist = [shift("1", at(1), 0, auto_closed=True), shift("1", at(400), 3)]
        m = ta.availability_matrix(hist, NOW, weeks=4)
        self.assertTrue(all(v == 0 for row in m["cells"] for v in row))


class PerformanceCase(unittest.TestCase):
    def test_absence_prorates_hours_and_missing_signals_renormalize(self):
        full = {"id": "1", "name": "A", "hours": 6, "tickets": 10, "tasks_done": 5, "trainings": 3, "rating": 4.5, "absent_days": 15}
        out = ta.performance([full])[0]
        self.assertEqual(out["score"], 100)  # 12 h target halves to 6 h
        sparse = {"id": "2", "name": "B", "hours": 12, "tickets": 10, "tasks_done": 5, "trainings": 3, "rating": None}
        out = ta.performance([sparse])[0]
        self.assertEqual(out["score"], 100)
        self.assertEqual(out["missing"], ["rating"])

    def test_warnings_are_shown_but_do_not_change_the_score(self):
        row = {"id": "1", "name": "A", "hours": 12, "tickets": 10, "tasks_done": 5, "trainings": 3, "warns": 0}
        self.assertEqual(ta.performance([row])[0]["score"], ta.performance([{**row, "warns": 3}])[0]["score"])
        self.assertEqual(ta.performance([{**row, "warns": 3}])[0]["warns"], 3)

    def test_fully_absent_person_is_not_penalized_for_hours(self):
        out = ta.performance([{"id": "1", "name": "A", "hours": 0, "absent_days": 30, "tickets": 10, "tasks_done": 5, "trainings": 3}])[0]
        self.assertNotIn("hours", out["parts"])
        self.assertEqual(ta.performance([])[:1], [])


class AbsenceCalendarCase(unittest.TestCase):
    def test_absence_wins_over_shift_and_both_date_formats(self):
        start = date(2026, 11, 15)
        hist = [shift("1", datetime(2026, 11, 16, 10, tzinfo=UTC), 2), shift("2", datetime(2026, 11, 16, 10, tzinfo=UTC), 2)]
        cal = ta.absence_calendar([("1", "Ann"), ("2", "Bo")], {"1": {"von": "2026-11-16", "bis": "2026-11-17"},
                                                               "2": {"von": "20.11.2026", "bis": "21.11.2026"}},
                                  hist, events=[{"day": date(2026, 11, 18), "user_ids": ["2"]}], start=start, days=7)
        rows = {r["id"]: r["days"] for r in cal["rows"]}
        self.assertEqual(rows["1"]["2026-11-16"], "absent")
        self.assertEqual(rows["1"]["2026-11-17"], "absent")
        self.assertEqual(rows["2"]["2026-11-16"], "shift")
        self.assertEqual(rows["2"]["2026-11-18"], "event")
        self.assertEqual(rows["2"]["2026-11-20"], "absent")
        self.assertEqual(len(cal["dates"]), 7)

    def test_broken_absence_and_unknown_person_are_ignored(self):
        cal = ta.absence_calendar([("1", "Ann")], {"1": {"von": "?", "bis": "?"}, "7": {"von": "2026-11-16", "bis": "2026-11-17"}}, [], start=date(2026, 11, 15))
        self.assertEqual(cal["rows"][0]["days"], {})


class ReportCase(unittest.TestCase):
    def test_week_report_compares_last_full_weeks(self):
        # NOW = Sunday 15.11. -> last full week Mon 02.11. - Sun 08.11. ... wait: weekday()=6, end = Mon 09.11.
        hist = [shift("1", datetime(2026, 11, 3, 10, tzinfo=UTC), 2), shift("2", datetime(2026, 11, 4, 10, tzinfo=UTC), 4),
                shift("1", datetime(2026, 10, 27, 10, tzinfo=UTC), 1), shift("1", datetime(2026, 11, 10, 10, tzinfo=UTC), 9)]
        tickets = [{"claimed_by_id": "1", "status": "closed", "closed_at": "2026-11-05T10:00:00+00:00"},
                   {"claimed_by_id": "", "status": "closed", "closed_at": "2026-11-05T10:00:00+00:00"}]
        tasks = [{"status": "done", "updated_at": "2026-11-06T10:00:00+00:00"}]
        rep = ta.report(hist, tickets, tasks, NOW, "week")
        self.assertEqual((rep["start"], rep["end"]), ("2026-11-02", "2026-11-08"))
        self.assertEqual(rep["current"], {"hours": 6.0, "shifts": 2, "active": 2, "avg_shift_h": 3.0, "tickets_closed": 1, "tasks_done": 1})
        self.assertEqual(rep["previous"]["hours"], 1.0)
        self.assertEqual(rep["delta"]["hours"], 5.0)
        self.assertIn("hours", rep["definitions"])

    def test_month_report_and_empty_data(self):
        rep = ta.report([], [], [], NOW, "month")
        self.assertEqual((rep["start"], rep["end"]), ("2026-10-01", "2026-10-31"))
        self.assertIsNone(rep["current"]["avg_shift_h"])
        self.assertIsNone(rep["delta"]["avg_shift_h"])


class GoalsCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = str(Path(self.tmp.name) / "pulse.db")
        self.patch = patch.object(pulse_db, "DB_PATH", self.db)
        self.patch.start()
        pulse_db._SCHEMA_READY.discard(self.db)
        pulse_db.init_db()

    def tearDown(self):
        pulse_db._SCHEMA_READY.discard(self.db)
        self.patch.stop()
        self.tmp.cleanup()

    def test_add_list_and_status_only_for_the_owner(self):
        g = ta.add_goal("1", "Moderations-Schulung", "bis Dezember", "15.12.2026", "9", "Lead")
        self.assertEqual(g["due"], "2026-12-15")
        ta.add_goal("2", "Anderes Ziel")
        self.assertEqual([x["title"] for x in ta.goals("1")], ["Moderations-Schulung"])
        self.assertFalse(ta.set_goal_status(g["id"], "2", "done"))  # another person's goal is untouched
        self.assertEqual(ta.goals("1")[0]["status"], "open")
        self.assertTrue(ta.set_goal_status(g["id"], "1", "done"))
        self.assertEqual(ta.goals("1")[0]["status"], "done")

    def test_validation(self):
        for bad in (("1", ""), ("1", "x" * 161), ("1", "ok", "", "kein-datum")):
            with self.assertRaises(ValueError):
                ta.add_goal(*bad)
        with self.assertRaises(ValueError):
            ta.set_goal_status("x", "1", "weird")
        self.assertEqual(ta.goals("nobody"), [])

    def test_goals_are_part_of_backup_and_restore(self):
        ta.add_goal("1", "Ziel")
        state = pulse_db.export_state()
        self.assertEqual(len(state["team_dev_goals"]), 1)
        with pulse_db.connect() as db:
            db.execute("DELETE FROM team_dev_goals")
        pulse_db.import_state(state)
        self.assertEqual(len(ta.goals("1")), 1)


class PackageCase(unittest.TestCase):
    STATS = {"hours_30d": 20.0, "tickets_30d": 12, "rating_avg": None, "trainings": 3, "active_warns": 0,
             "warn_free_days": None, "days_in_team": 90, "probation_active": False}

    def test_package_lists_met_missing_gaps_and_trend(self):
        crit = pulse_insights.normalize_criteria({})["default"]
        ready = pulse_insights.readiness(self.STATS, crit)
        pkg = ta.promotion_package(self.STATS, ready, 10.0, [{"status": "open", "title": "x"}, {"status": "done"}], warns_visible=False)
        self.assertEqual(pkg["ready"], ready["ready"])
        self.assertEqual(len(pkg["met"]) + len(pkg["missing"]), len(ready["checks"]))
        self.assertEqual(pkg["trend"]["direction"], "steigend")
        self.assertEqual(len(pkg["open_goals"]), 1)
        self.assertIn("keine Ticket-Bewertungen vorhanden", pkg["gaps"])
        self.assertIn("Warnungen für dich nicht einsehbar", pkg["gaps"])
        self.assertIn("keine automatische", pkg["note"])

    def test_no_previous_data_gives_no_trend(self):
        ready = pulse_insights.readiness(self.STATS, pulse_insights.normalize_criteria({})["default"])
        self.assertIsNone(ta.promotion_package(self.STATS, ready, None, [], True)["trend"])


class DossierCase(unittest.TestCase):
    def test_dossier_hides_warnings_when_not_visible(self):
        d = ta.dossier(uid=1, name="Ann", stats={"hours_30d": 5.0}, workload_row=None, anomalies=[1, 2], goals_=[{"status": "open"}, {"status": "done"}],
                       absence=None, warns=None)
        self.assertIsNone(d["warns"])
        self.assertEqual((d["anomalies"], d["goals_open"], d["id"]), (2, 1, "1"))


if __name__ == "__main__":
    unittest.main()
