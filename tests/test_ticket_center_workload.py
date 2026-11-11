"""Leadership-only ticket workload summary rendering tests."""
import html
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from starlette.datastructures import QueryParams

from pulse_ticket_center import render_ticket_center


class TicketCenterWorkloadTests(unittest.TestCase):
    def setUp(self):
        self.tickets = [
            {"id": "T-1", "guild_id": "1", "status": "open", "priority": "urgent",
             "claimed_by_id": "10", "claimed_by_name": "Alice", "user_name": "Spieler A",
             "category": "support", "opened_at": "2026-11-11T10:00:00+00:00"},
            {"id": "T-2", "guild_id": "1", "status": "in_progress", "priority": "normal",
             "claimed_by_id": "10", "claimed_by_name": "Alice", "user_name": "Spieler B",
             "category": "support", "opened_at": "2026-11-11T10:01:00+00:00"},
        ]
        self.ws = SimpleNamespace(
            esc=lambda value: html.escape(str(value or "")),
            pulse_ticket_config=lambda: {
                "automation": {},
                "distribution": {"enabled": False, "max_open_per_staff": 2},
            },
            pulse_ticket_category_registry=lambda: {"support": {"name": "Support", "emoji": "🎫"}},
        )
        self.request = SimpleNamespace(query_params=QueryParams(""))

    def render(self, permissions):
        ctx = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user={"id": "99"},
            perms=permissions,
        )
        with (
            patch("pulse_db.list_tickets", return_value=self.tickets),
            patch("pulse_views.bar", return_value=""),
            patch("pulse_ticket_extras.tags_for", return_value={}),
        ):
            return render_ticket_center(self.ws, ctx, self.request, lambda _ws, _title, _ctx, _section, body: body)

    def test_managers_see_workload_and_capacity_warning(self):
        body = self.render({"can_manage_tickets": True})
        self.assertIn("Team-Auslastung", body)
        self.assertIn("Alice", body)
        self.assertIn("2/2 offene Tickets", body)
        self.assertIn("Kapazitätsgrenze", body)
        self.assertIn("Automatische Zuweisung ist deaktiviert", body)

    def test_viewers_do_not_receive_staff_workload_panel(self):
        body = self.render({"can_view_dashboard": True})
        self.assertNotIn("Team-Auslastung", body)
        # Der Bearbeitername steht weiterhin in der normalen Ticketliste; gesperrt sind
        # nur die Auslastungszahlen und Kapazitätswarnungen je Person.
        self.assertNotIn("Lastpunkte", body)
        self.assertNotIn("2/2 offene Tickets", body)
        self.assertNotIn("Kapazitätsgrenze", body)

    def test_viewers_never_trigger_the_workload_calculation(self):
        with patch("pulse_ticket_center.workload_snapshot") as snapshot:
            self.render({"can_view_dashboard": True})
        snapshot.assert_not_called()

    def test_foreign_guild_tickets_do_not_count_towards_the_panel(self):
        self.tickets.append(
            {"id": "T-9", "guild_id": "2", "status": "open", "priority": "urgent",
             "claimed_by_id": "77", "claimed_by_name": "Fremdserver-Mitarbeiter",
             "user_name": "Spieler X", "category": "support",
             "opened_at": "2026-11-11T10:02:00+00:00"})
        body = self.render({"can_manage_tickets": True})
        self.assertNotIn("Fremdserver-Mitarbeiter", body)
        self.assertIn("2/2 offene Tickets", body)


if __name__ == "__main__":
    unittest.main()
