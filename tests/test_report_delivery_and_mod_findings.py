"""Audit #88/#129/#137: automatische Berichtszustellung (Funktionsschalter, einmal je Zeitraum) und Moderations-Hinweise."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import pulse_intel as intel
import pulse_report_schedule as rs
from tests.fakes import FakeGuild, FakeMember, FakeRole

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)   # Montag
LEAD_ID, MEMBER_ID = 900000000000000001, 900000000000000002
ROLE_LEAD, ROLE_TEAM = FakeRole(10, "Leitung"), FakeRole(11, "Support")
CONFIG = {"team_role_ids": [10, 11], "permissions": {"10": {"can_promote": True}, "11": {}}}


class DbBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = f"{self.tmp.name}/pulse.db"
        patcher = patch.object(pulse_db, "DB_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)
        pulse_db._SCHEMA_READY.discard(path)
        pulse_db.init_db()
        rs._last_check = 0.0


class DeliveryTests(DbBase):
    def setUp(self):
        super().setUp()
        self.guild = FakeGuild(members=[FakeMember(LEAD_ID, "Lea", roles=[ROLE_LEAD]), FakeMember(MEMBER_ID, "Max", roles=[ROLE_TEAM])], roles=[ROLE_LEAD, ROLE_TEAM])
        self.ws = SimpleNamespace(load_shifts_raw=lambda: {"history": []})

    def inbox(self, uid):
        return pulse_db.notifications(str(uid))

    def test_disabled_flag_sends_nothing_and_remembers_nothing(self):
        self.assertEqual(rs.run(self.ws, self.guild, CONFIG, NOW, enabled=False, force=True), [])
        self.assertEqual(self.inbox(LEAD_ID), [])
        self.assertEqual(pulse_db.get_setting(rs.STATE_KEY, {}), {})

    def test_week_and_month_go_to_leadership_only_once_per_period(self):
        self.assertEqual(rs.run(self.ws, self.guild, CONFIG, NOW, enabled=True, force=True), ["week", "month"])
        titles = [n["title"] for n in self.inbox(LEAD_ID)]
        self.assertTrue(any("Wochenbericht" in t for t in titles) and any("Monatsbericht" in t for t in titles))
        self.assertTrue(all(n["url"] == rs.URL for n in self.inbox(LEAD_ID)))
        self.assertEqual(self.inbox(MEMBER_ID), [])                     # normales Teammitglied bekommt nichts
        self.assertEqual(rs.run(self.ws, self.guild, CONFIG, NOW, enabled=True, force=True), [])    # gleicher Zeitraum
        self.assertEqual(len(self.inbox(LEAD_ID)), 2)
        later = NOW + timedelta(days=7)                                  # nächste volle Woche (Monat bleibt)
        self.assertEqual(rs.run(self.ws, self.guild, CONFIG, later, enabled=True, force=True), ["week"])

    def test_check_interval_and_missing_guild(self):
        self.assertEqual(rs.run(self.ws, None, CONFIG, NOW, enabled=True, force=True), [])
        self.assertEqual(rs.run(self.ws, self.guild, CONFIG, NOW, enabled=True), ["week", "month"])
        self.assertEqual(rs.run(self.ws, self.guild, CONFIG, NOW + timedelta(days=7), enabled=True), [])   # Prüfintervall (5 Min.) noch nicht um

    def test_a_failing_delivery_is_retried_not_marked_done(self):
        with patch("pulse_inbox_contract.deliver", side_effect=RuntimeError("x")):
            self.assertEqual(rs.run(self.ws, self.guild, CONFIG, NOW, enabled=True, force=True), [])
        self.assertEqual(pulse_db.get_setting(rs.STATE_KEY, {}), {})
        self.assertEqual(rs.run(self.ws, self.guild, CONFIG, NOW, enabled=True, force=True), ["week", "month"])

    def test_nobody_eligible_still_marks_the_period(self):
        empty = FakeGuild(members=[FakeMember(MEMBER_ID, "Max", roles=[ROLE_TEAM])], roles=[ROLE_TEAM])
        self.assertEqual(rs.run(self.ws, empty, CONFIG, NOW, enabled=True, force=True), ["week", "month"])
        self.assertEqual(rs.run(self.ws, empty, CONFIG, NOW, enabled=True, force=True), [])

    def test_flag_is_registered_and_off_by_default(self):
        import pulse_flags
        self.assertIn("report_delivery", pulse_flags.FEATURES)
        self.assertFalse(pulse_flags.enabled("report_delivery"))

    def test_due_kinds_and_summary(self):
        reports = {"week": {"start": "2026-11-09"}, "month": {"start": "2026-10-01"}}
        self.assertEqual(rs.due_kinds({"week": "2026-11-09"}, reports), ["month"])
        self.assertEqual(rs.due_kinds("kaputt", reports), ["week", "month"])
        title, body = rs.summary({"start": "a", "end": "b", "current": {"Stunden": 12.5, "Tickets": None}, "previous": {"Stunden": 10, "Tickets": 3}}, "Wochenbericht")
        self.assertIn("Wochenbericht a bis b", title)
        self.assertIn("Tickets: – (vorher 3)", body)


def ts(days_ago=0, hours_ago=0):
    return (NOW - timedelta(days=days_ago, hours=hours_ago)).isoformat()


class ModerationFindingTests(unittest.TestCase):
    def ids(self, data):
        return {f["id"]: f for f in intel.findings(data, NOW)}

    def test_nothing_without_sources(self):
        found = self.ids({})
        for fid in ("mod-spike", "reports-stale", "appeals-pending", "cases-urgent"):
            self.assertNotIn(fid, found)

    def test_moderation_spike_uses_the_weekday_baseline(self):
        cases = [{"created_at": ts(hours_ago=1)} for _ in range(8)] + [{"created_at": ts(days_ago=7)}]
        found = self.ids({"mod_cases": cases})
        self.assertEqual(found["mod-spike"]["area"], "Moderation")
        self.assertIn("Schwelle", found["mod-spike"]["why"])
        self.assertNotIn("mod-spike", self.ids({"mod_cases": [{"created_at": ts(hours_ago=1)}]}))   # unter dem Mindestwert

    def test_stale_reports_appeals_and_urgent_cases(self):
        data = {"reports": [{"status": "open", "created_at": ts(hours_ago=60)}] * 3 + [{"status": "closed", "created_at": ts(days_ago=10)}, {"status": "open", "created_at": ts(hours_ago=2)}],
                "appeals": [{"status": "pending", "submitted_at": ts(days_ago=5)}, {"status": "pending", "submitted_at": ts(hours_ago=3)}, {"status": "accepted", "submitted_at": ts(days_ago=9)}],
                "cases": [{"priority": "urgent", "status": "open"}, {"priority": "normal", "status": "open"}, {"priority": "high", "status": "closed"}]}
        found = self.ids(data)
        self.assertEqual((found["reports-stale"]["severity"], "3 Spielermeldung" in found["reports-stale"]["title"]), ("hoch", True))
        self.assertIn("1 Einspruch", found["appeals-pending"]["title"])
        self.assertEqual((found["cases-urgent"]["severity"], found["cases-urgent"]["next"]["url"]), ("hoch", "/ultimate/cases"))

    def test_garbage_rows_never_raise(self):
        junk = {"mod_cases": [None, "x", {"created_at": "kaputt"}], "reports": [None, {}], "appeals": ["x"], "cases": [1, {}]}
        self.assertIsInstance(intel.findings(junk, NOW), list)

    def test_sources_tolerate_failing_loaders(self):
        with patch("pulse_mod_cases.list_cases", side_effect=RuntimeError("x")), patch("pulse_player_reports.list_reports", side_effect=RuntimeError("x")):
            sources = intel.moderation_sources(1)
        self.assertEqual((sources["mod_cases"], sources["reports"]), ([], []))
        self.assertEqual(set(sources), {"mod_cases", "reports", "appeals", "cases"})


if __name__ == "__main__":
    unittest.main()
