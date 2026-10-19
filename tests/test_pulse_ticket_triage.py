import unittest
from datetime import datetime, timedelta, timezone

from pulse_ticket_triage import format_wait, matches_filter, triage

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)
SETTINGS = {"unclaimed_ping_minutes": 30, "staff_reply_hours": 12, "user_reminder_hours": 24}


def ago(**kw):
    return (NOW - timedelta(**kw)).isoformat()


class TriageTests(unittest.TestCase):
    def test_unclaimed_ticket_waits_for_takeover(self):
        info = triage({"status": "open", "opened_at": ago(minutes=45)}, NOW, SETTINGS)
        self.assertEqual(info["state"], "new")
        self.assertTrue(info["overdue"])
        self.assertIn("seit 45 Min.", info["reason"])
        self.assertEqual(info["next_step"], "Ticket übernehmen.")

    def test_creator_waits_for_team(self):
        t = {"status": "in_progress", "claimed_by_id": "1", "opened_at": ago(days=1),
             "last_staff_message_at": ago(hours=5), "last_user_message_at": ago(hours=2)}
        info = triage(t, NOW, SETTINGS)
        self.assertEqual(info["state"], "waiting_team")
        self.assertFalse(info["overdue"])
        self.assertEqual(info["next_step"], "Dem Ersteller antworten.")

    def test_team_waits_for_creator_and_overdue_threshold(self):
        t = {"status": "in_progress", "claimed_by_id": "1", "opened_at": ago(days=3),
             "last_staff_message_at": ago(hours=30), "last_user_message_at": ago(hours=40)}
        info = triage(t, NOW, SETTINGS)
        self.assertEqual(info["state"], "waiting_user")
        self.assertTrue(info["overdue"])

    def test_missing_activity_is_not_guessed(self):
        t = {"status": "in_progress", "claimed_by_id": "1", "opened_at": "kaputt"}
        info = triage(t, NOW, SETTINGS)
        self.assertEqual(info["state"], "in_progress")
        self.assertIsNone(info["waited_seconds"])
        self.assertEqual(info["reason"], "Keine Zeitangabe vorhanden.")
        self.assertFalse(info["overdue"])

    def test_sorting_overdue_then_priority_then_wait(self):
        overdue_low = triage({"status": "open", "priority": "low", "opened_at": ago(hours=2)}, NOW, SETTINGS)
        fresh_urgent = triage({"status": "open", "priority": "urgent", "opened_at": ago(minutes=5)}, NOW, SETTINGS)
        closed = triage({"status": "closed", "priority": "urgent", "closed_at": ago(minutes=1)}, NOW, SETTINGS)
        ordered = sorted([closed, fresh_urgent, overdue_low], key=lambda i: i["sort_key"])
        self.assertEqual(ordered, [overdue_low, fresh_urgent, closed])

    def test_filters_keep_existing_status_keys(self):
        open_t = {"status": "open"}
        mine = {"status": "in_progress", "claimed_by_id": "7"}
        closed_t = {"status": "closed"}
        info_open, info_mine, info_closed = (triage(t, NOW, SETTINGS) for t in (open_t, mine, closed_t))
        self.assertTrue(matches_filter(open_t, info_open, "open", "7"))
        self.assertFalse(matches_filter(mine, info_mine, "open", "7"))
        self.assertTrue(matches_filter(mine, info_mine, "claimed", "7"))
        self.assertTrue(matches_filter(mine, info_mine, "mine", "7"))
        self.assertFalse(matches_filter(mine, info_mine, "mine", "8"))
        self.assertTrue(matches_filter(closed_t, info_closed, "closed", "7"))
        self.assertFalse(matches_filter(closed_t, info_closed, "active", "7"))
        self.assertTrue(matches_filter(closed_t, info_closed, "all", "7"))

    def test_format_wait(self):
        self.assertEqual(format_wait(None), "unbekannt")
        self.assertEqual(format_wait(59 * 60), "59 Min.")
        self.assertEqual(format_wait(3 * 3600 + 5 * 60), "3 Std. 5 Min.")
        self.assertEqual(format_wait(30 * 3600), "30 Std.")
        self.assertEqual(format_wait(3 * 86400), "3 Tagen")


if __name__ == "__main__":
    unittest.main()
