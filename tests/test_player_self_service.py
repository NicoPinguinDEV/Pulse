"""Discord self-service views for personal tickets, calendar RSVPs and notifications."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_db
import pulse_ultimate
from cogs.pulse_pro import PulsePro


class PlayerSelfServiceDataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.init_patch = patch.object(pulse_ultimate, "_INIT", False)
        self.db_patch.start()
        self.init_patch.start()
        pulse_db.init_db()
        pulse_ultimate.init_db()

    def tearDown(self):
        self.init_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_ticket_listing_is_scoped_to_both_user_and_guild(self):
        own_id = pulse_db.create_ticket("channel-own", "guild-a", "42", "Muster", "Support")
        pulse_db.create_ticket("channel-other-user", "guild-a", "43", "Andere Person", "Bewerbung")
        pulse_db.create_ticket("channel-other-guild", "guild-b", "42", "Muster", "Allgemein")

        rows = pulse_db.list_user_tickets("guild-a", 42, limit=10)

        self.assertEqual([row["id"] for row in rows], [own_id])
        self.assertEqual(
            set(rows[0]),
            {"id", "channel_id", "category", "status", "priority", "opened_at", "closed_at"},
        )
        self.assertNotIn("user_id", rows[0])
        self.assertNotIn("transcript_path", rows[0])

    def test_ticket_listing_rejects_missing_identity_and_bounds_limit(self):
        self.assertEqual(pulse_db.list_user_tickets("", 42), [])
        self.assertEqual(pulse_db.list_user_tickets("guild-a", None), [])

        for index in range(30):
            pulse_db.create_ticket(
                f"channel-{index}", "guild-a", "42", "Muster", "Support"
            )
        self.assertEqual(len(pulse_db.list_user_tickets("guild-a", "42", limit=1000)), 25)

    def test_event_rsvp_listing_returns_only_callers_upcoming_responses(self):
        future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(timespec="seconds")
        past = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
        with pulse_ultimate.cx() as connection:
            for event_id, title, starts_at in (
                ("event-own", "Eigener Termin", future),
                ("event-other", "Anderer Termin", future),
                ("event-past", "Vergangener Termin", past),
            ):
                connection.execute(
                    "INSERT INTO ultimate_calendar VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        event_id, title, "event", starts_at, None, "Beschreibung",
                        "Discord", None, "999", "Leitung", starts_at,
                    ),
                )
            connection.executemany(
                """INSERT INTO ultimate_calendar_rsvps
                   (event_id,user_id,display_name,response,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                [
                    ("event-own", "42", "Muster", "going", future, future),
                    ("event-other", "43", "Andere Person", "declined", future, future),
                    ("event-past", "42", "Muster", "going", past, past),
                ],
            )

        rows = pulse_ultimate.list_user_calendar_rsvps(42, limit=10)

        self.assertEqual([row["event_id"] for row in rows], ["event-own"])
        self.assertEqual(rows[0]["response"], "going")
        self.assertNotIn("user_id", rows[0])
        self.assertNotIn("display_name", rows[0])


class PlayerSelfServiceCommandTests(unittest.TestCase):
    def interaction(self, *, guild_id=123, user_id=42):
        return SimpleNamespace(
            guild=SimpleNamespace(id=guild_id) if guild_id is not None else None,
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def test_my_tickets_uses_only_current_user_and_guild_and_is_private(self):
        interaction = self.interaction(guild_id=77, user_id=42)
        rows = [{
            "id": "TICKET-OWN",
            "channel_id": "998877",
            "category": "Support",
            "status": "open",
            "priority": "high",
            "opened_at": "2030-01-01T12:00:00+00:00",
            "closed_at": None,
        }]
        with patch("cogs.pulse_pro.db.list_user_tickets", return_value=rows) as query:
            asyncio.run(PulsePro.my_tickets.callback(PulsePro.__new__(PulsePro), interaction))

        query.assert_called_once_with(77, 42, limit=10)
        interaction.response.send_message.assert_awaited_once()
        sent = interaction.response.send_message.await_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertIn("TICKET-OWN", sent["embed"].fields[0].name)
        self.assertIn("<#998877>", sent["embed"].fields[0].value)

    def test_my_events_uses_only_current_user_and_is_private(self):
        interaction = self.interaction(guild_id=77, user_id=42)
        rows = [{
            "event_id": "event-1",
            "title": "Teamabend",
            "kind": "event",
            "start_at": "2030-01-01T18:00:00+00:00",
            "location": "Discord",
            "response": "going",
        }]
        with patch("pulse_ultimate.list_user_calendar_rsvps", return_value=rows) as query:
            asyncio.run(PulsePro.my_events.callback(PulsePro.__new__(PulsePro), interaction))

        query.assert_called_once_with(42, limit=10)
        interaction.response.send_message.assert_awaited_once()
        sent = interaction.response.send_message.await_args.kwargs
        self.assertTrue(sent["ephemeral"])
        self.assertIn("Teamabend", sent["embed"].fields[0].name)
        self.assertIn("Zugesagt", sent["embed"].fields[0].value)

    def test_my_notifications_reads_only_callers_settings_and_is_private(self):
        interaction = self.interaction(user_id=42)
        prefs = {
            "dm": True,
            "muted": ["tickets"],
            "quiet_from": 22,
            "quiet_to": 7,
        }
        with patch("pulse_notify_prefs.load", return_value=prefs) as load:
            asyncio.run(PulsePro.my_notifications.callback(PulsePro.__new__(PulsePro), interaction))

        load.assert_called_once_with(42)
        interaction.response.send_message.assert_awaited_once()
        sent = interaction.response.send_message.await_args.kwargs
        self.assertTrue(sent["ephemeral"])
        rendered = " ".join(
            [sent["embed"].title]
            + [field.name + " " + field.value for field in sent["embed"].fields]
        )
        self.assertIn("Aktiviert", rendered)
        self.assertIn("22:00–07:00", rendered)
        self.assertIn("Tickets", rendered)

    def test_self_service_commands_do_not_query_personal_data_outside_a_guild(self):
        interaction = self.interaction(guild_id=None)
        with patch("cogs.pulse_pro.db.list_user_tickets") as query:
            asyncio.run(PulsePro.my_tickets.callback(PulsePro.__new__(PulsePro), interaction))
        query.assert_not_called()
        sent = interaction.response.send_message.await_args.kwargs
        self.assertTrue(sent["ephemeral"])


if __name__ == "__main__":
    unittest.main()
