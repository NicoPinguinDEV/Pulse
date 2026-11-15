"""Audit #129-#132/#136/#137/#139: local rule-based leadership intelligence (synthetic data, no AI, no network)."""
import unittest
from datetime import datetime, timedelta, timezone

import pulse_intel as intel

UTC = timezone.utc
NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)  # Monday


def ticket(hours_ago, status="open", priority="normal", claimed="", name="", **extra):
    row = {"id": f"t{hours_ago}{status}{priority}{claimed}", "opened_at": (NOW - timedelta(hours=hours_ago)).isoformat(), "status": status,
           "priority": priority, "claimed_by_id": claimed, "claimed_by_name": name, "user_name": "Spieler", "category": "Support"}
    row.update(extra)
    return row


def shift(uid, days_ago, hours):
    start = (NOW - timedelta(days=days_ago)).replace(hour=10)
    return {"mod_id": uid, "mod_name": f"M{uid}", "started_at_iso": start.isoformat(), "duration_seconds": int(hours * 3600)}


def ids(found):
    return [f["id"] for f in found]


class SpikeAndFindingsCase(unittest.TestCase):
    def test_spike_needs_volume_and_deviation_from_same_weekday(self):
        base = [ticket(24 * 7 * w + 2, "closed") for w in range(1, 7)]
        today = [ticket(i, "closed") for i in range(1, 7)]  # 6 tickets within the last hours of today
        spike = intel.ticket_spike(base + today, NOW)
        self.assertEqual(spike["today"], 6)
        self.assertIsNone(intel.ticket_spike(base + today[:3], NOW))  # below the minimum
        steady = [ticket(24 * 7 * w + h, "closed") for w in range(1, 7) for h in (1, 2, 3, 4, 5, 6)]
        self.assertIsNone(intel.ticket_spike(steady + today, NOW))  # 6 is normal for this weekday

    def test_findings_are_explained_sorted_and_link_only(self):
        data = {"tickets": [ticket(30, claimed="")],
                "tasks": [], "history": [], "members": [("5", "Eve")]}
        data["tickets"][1:] = [dict(ticket(2, "open", "urgent", "5", "Eve"), id=f"x{i}") for i in range(8)]
        found = intel.findings(data, NOW)
        self.assertIn("tickets-overdue", ids(found))
        self.assertIn("tickets-unclaimed", ids(found))
        self.assertIn("workload", ids(found))
        order = [intel.SEVERITY_ORDER[f["severity"]] for f in found]
        self.assertEqual(order, sorted(order))
        for f in found:
            self.assertTrue(f["why"] and f["next"]["url"].startswith("/"))
            self.assertEqual(set(f), {"id", "severity", "area", "title", "why", "next"})

    def test_quiet_data_has_no_findings_and_garbage_is_tolerated(self):
        self.assertEqual(intel.findings({}, NOW), [])
        self.assertEqual(intel.findings({"tickets": [{}, {"opened_at": "kaputt"}], "tasks": [None], "history": ["x", None], "members": []}, NOW), [])

    def test_hours_drop_and_overdue_tasks_and_stale_applications(self):
        history = [shift("1", d, 4) for d in (9, 10, 11, 12)] + [shift("1", 3, 1)]  # prev week 16 h, last full week 1 h
        tasks = [{"status": "open", "due_at": (NOW - timedelta(days=1)).isoformat(), "assignee_id": "1"} for _ in range(3)]
        apps = {"a": {"status": "pending", "created_at": "01.11.2026 10:00", "name": "X"}}
        found = intel.findings({"history": history, "tasks": tasks, "tickets": [], "members": [("1", "A")], "apps": apps}, NOW)
        expected = {"hours-drop", "tasks-overdue"} | ({"apps-stale"} if intel.recruiting else set())
        self.assertTrue(expected <= set(ids(found)), ids(found))


class DraftCase(unittest.TestCase):
    DATA = {"tickets": [ticket(30, "closed", closed_at=(NOW - timedelta(hours=20)).isoformat()), ticket(5, claimed="9", name="Bo"), ticket(40, "open")],
            "tasks": [{"title": "Regelwerk prüfen", "status": "open", "priority": "high", "assignee_name": "Ann", "due_at": (NOW - timedelta(days=1)).isoformat()}],
            "history": [shift("1", 1, 3)], "members": [("1", "Ann")]}

    def test_daily_draft_has_numbers_sources_and_is_a_draft(self):
        d = intel.daily_draft(self.DATA, NOW)
        text = "\n".join(d["lines"])
        self.assertIn("Entwurf", text)
        self.assertIn("Dienst: 3 h von 1 Person", text)
        self.assertIn("Aktuell offen: 2 Ticket", text)
        self.assertTrue(d["sources"])

    def test_weekly_draft_compares_with_previous_week(self):
        d = intel.weekly_draft({"history": [shift("1", 1, 2), shift("1", 8, 1)], "tickets": [], "tasks": []}, NOW)
        text = "\n".join(d["lines"])
        self.assertIn("Wochenbericht 2026-11-09 bis 2026-11-15", text)
        self.assertIn("Dienstzeit: 2.0 h (Vorwoche 1.0 h, +1)", text)
        self.assertIn("hours", d["definitions"])

    def test_handover_lists_open_work_oldest_first_and_limits(self):
        d = intel.handover_draft(self.DATA, NOW, limit=1)
        text = "\n".join(d["lines"])
        self.assertIn("Offene Tickets: 2", text)
        self.assertIn("… und 1 weitere", text)
        self.assertIn("Regelwerk prüfen · Ann · high · fällig 15.11. (überfällig)", text)
        self.assertLess(text.index("seit 40 h"), len(text))
        self.assertIn("Nächster Schritt", text)

    def test_empty_data_never_raises(self):
        for fn in (intel.daily_draft, intel.weekly_draft, intel.handover_draft):
            self.assertTrue(fn({}, NOW)["lines"])


class AskCase(unittest.TestCase):
    DATA = {"tickets": [ticket(30), ticket(2, claimed="9", name="Bo"), ticket(3, "closed")], "tasks": [], "history": [], "members": [("9", "Bo")],
            "apps": {"a": {"status": "pending", "created_at": "01.11.2026 10:00", "name": "Erika"}}}

    def test_supported_questions(self):
        cases = {"Welche Tickets sind überfällig?": ("overdue", "1 offene"), "Welche Tickets haben keinen Bearbeiter?": ("unclaimed", "1 offene"),
                 "Wie viele Tickets sind offen?": ("open", "2 Ticket"), "Welche Bewerbungen warten?": ("applications", "Bewerbung"),
                 "Wer ist überlastet?": ("workload", "Niemand"), "Gibt es auffällige Schichten?": ("anomalies", "0 auffällige")}
        for question, (intent, text) in cases.items():
            result = intel.ask(question, self.DATA, NOW)
            self.assertEqual(result["intent"], intent, question)
            self.assertIn(text, result["answer"], question)
            self.assertTrue(result["sources"])

    def test_unknown_question_is_answered_honestly_with_examples(self):
        result = intel.ask("Wie wird das Wetter?", self.DATA, NOW)
        self.assertIsNone(result["intent"])
        self.assertEqual(len(result["rows"]), len(intel.EXAMPLES))
        self.assertEqual(result["sources"], [])
        self.assertIsNone(intel.ask("", {}, NOW)["intent"])

    def test_rows_are_links_only(self):
        for text, url in intel.ask("überfällig", self.DATA, NOW)["rows"]:
            self.assertTrue(url.startswith("/"))


if __name__ == "__main__":
    unittest.main()
