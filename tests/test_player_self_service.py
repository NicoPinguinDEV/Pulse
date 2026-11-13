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

    def add_event(self, event_id, title, starts_at, kind="event"):
        with pulse_ultimate.cx() as connection:
            connection.execute(
                "INSERT INTO ultimate_calendar VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (event_id, title, kind, starts_at, None, "", "Discord", None, "999", "Leitung", starts_at),
            )

    def rsvps(self):
        with pulse_ultimate.cx() as connection:
            return [
                (row["event_id"], row["user_id"], row["response"])
                for row in connection.execute(
                    "SELECT event_id,user_id,response FROM ultimate_calendar_rsvps ORDER BY event_id,user_id"
                )
            ]

    def test_shared_rsvp_writes_changes_and_removes_only_own_row(self):
        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds")
        self.add_event("ev-1", "Teamabend", future)
        pulse_ultimate.set_calendar_rsvp("ev-1", 43, "Andere Person", "declined")

        event = pulse_ultimate.set_calendar_rsvp("ev-1", 42, "Muster", "going", require_upcoming=True)
        self.assertEqual(event["title"], "Teamabend")
        pulse_ultimate.set_calendar_rsvp("ev-1", 42, "Muster", "declined", require_upcoming=True)
        self.assertEqual(self.rsvps(), [("ev-1", "42", "declined"), ("ev-1", "43", "declined")])

        pulse_ultimate.set_calendar_rsvp("ev-1", 42, "Muster", "remove", require_upcoming=True)
        self.assertEqual(self.rsvps(), [("ev-1", "43", "declined")])

    def test_shared_rsvp_rejects_invalid_input_like_the_dashboard(self):
        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds")
        past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
        self.add_event("deadline", "Frist", future, kind="deadline")
        self.add_event("past", "Vergangen", past)
        cases = [
            (("missing", 42, "M", "going"), {}, 404),
            (("deadline", 42, "M", "going"), {}, 400),
            (("past", 42, "M", "maybe"), {}, 400),
            (("past", "", "M", "going"), {}, 400),
            (("past", 42, "M", "going"), {"require_upcoming": True}, 400),
        ]
        for args, kwargs, status in cases:
            with self.subTest(args=args, kwargs=kwargs):
                with self.assertRaises(pulse_ultimate.CalendarRsvpError) as raised:
                    pulse_ultimate.set_calendar_rsvp(*args, **kwargs)
                self.assertEqual(raised.exception.status, status)
        self.assertEqual(self.rsvps(), [])
        # The dashboard keeps accepting RSVPs for events that have already started.
        pulse_ultimate.set_calendar_rsvp("past", 42, "M", "going")
        self.assertEqual(self.rsvps(), [("past", "42", "going")])

    def test_upcoming_rsvp_events_exclude_deadlines_past_and_attendees(self):
        future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat(timespec="seconds")
        later = (datetime.now(timezone.utc) + timedelta(days=5)).isoformat(timespec="seconds")
        past = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat(timespec="seconds")
        self.add_event("ev-a", "Teamabend", future)
        self.add_event("ev-b", "Schulung Funk", later, kind="schulung")
        self.add_event("ev-c", "Abgabefrist", future, kind="deadline")
        self.add_event("ev-d", "Alter Termin", past)
        pulse_ultimate.set_calendar_rsvp("ev-a", 43, "Andere Person", "going")

        rows = pulse_ultimate.list_upcoming_rsvp_events()
        self.assertEqual([row["id"] for row in rows], ["ev-a", "ev-b"])
        self.assertEqual(set(rows[0]), {"id", "title", "kind", "start_at"})
        self.assertEqual([row["id"] for row in pulse_ultimate.list_upcoming_rsvp_events("funk")], ["ev-b"])
        self.assertEqual(len(pulse_ultimate.list_upcoming_rsvp_events(limit=1)), 1)


class EventRsvpCommandTests(unittest.TestCase):
    def interaction(self, *, guild=True, user_id=42):
        return SimpleNamespace(
            guild=SimpleNamespace(id=77) if guild else None,
            user=SimpleNamespace(id=user_id, display_name="Muster"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    def run_command(self, interaction, response="going", *, perms=None, member=True, **patches):
        perms = {"can_view_dashboard": True} if perms is None else perms
        with patch("webserver.load_config", return_value={}), \
             patch("webserver.compute_perms", return_value=(perms, object() if member else None)):
            asyncio.run(PulsePro.event_rsvp.callback(
                PulsePro.__new__(PulsePro), interaction, "ev-1", SimpleNamespace(value=response)
            ))

    def test_without_dashboard_access_nothing_is_written(self):
        for perms, member in (({}, True), ({"can_view_dashboard": True}, False)):
            with self.subTest(perms=perms, member=member):
                interaction = self.interaction()
                with patch("pulse_ultimate.set_calendar_rsvp") as write:
                    self.run_command(interaction, perms=perms, member=member)
                write.assert_not_called()
                self.assertTrue(interaction.response.send_message.await_args.kwargs["ephemeral"])
        interaction = self.interaction(guild=False)
        with patch("pulse_ultimate.set_calendar_rsvp") as write:
            asyncio.run(PulsePro.event_rsvp.callback(
                PulsePro.__new__(PulsePro), interaction, "ev-1", SimpleNamespace(value="going")
            ))
        write.assert_not_called()

    def test_rsvp_writes_callers_row_only_for_upcoming_events_and_is_audited(self):
        interaction = self.interaction(user_id=42)
        event = {"id": "ev-1", "title": "Teamabend", "start_at": "2030-01-01T18:00:00+00:00"}
        with patch("pulse_ultimate.set_calendar_rsvp", return_value=event) as write, \
             patch("pulse_ultimate.audit") as audit:
            self.run_command(interaction, "declined")
        write.assert_called_once_with("ev-1", 42, "Muster", "declined", require_upcoming=True)
        audit.assert_called_once()
        self.assertEqual(audit.call_args.args[0].user["id"], "42")
        self.assertIn("(Discord)", audit.call_args.args[4])
        sent = interaction.response.send_message.await_args
        self.assertTrue(sent.kwargs["ephemeral"])
        self.assertIn("abgesagt", sent.args[0])

    def test_validation_errors_are_shown_privately(self):
        interaction = self.interaction()
        error = pulse_ultimate.CalendarRsvpError(400, "Der Termin hat bereits begonnen.")
        with patch("pulse_ultimate.set_calendar_rsvp", side_effect=error), \
             patch("pulse_ultimate.audit") as audit:
            self.run_command(interaction)
        audit.assert_not_called()
        sent = interaction.response.send_message.await_args
        self.assertIn("bereits begonnen", sent.args[0])
        self.assertTrue(sent.kwargs["ephemeral"])

    def test_autocomplete_offers_nothing_without_access(self):
        from cogs.pulse_pro import _calendar_rsvp_autocomplete
        interaction = self.interaction()
        with patch("webserver.load_config", return_value={}), \
             patch("webserver.compute_perms", return_value=({}, object())), \
             patch("pulse_ultimate.list_upcoming_rsvp_events") as query:
            self.assertEqual(asyncio.run(_calendar_rsvp_autocomplete(interaction, "")), [])
        query.assert_not_called()
        rows = [{"id": "ev-1", "title": "Teamabend", "kind": "event", "start_at": "2030-01-01T18:00:00+00:00"}]
        with patch("webserver.load_config", return_value={}), \
             patch("webserver.compute_perms", return_value=({"can_view_dashboard": True}, object())), \
             patch("pulse_ultimate.list_upcoming_rsvp_events", return_value=rows):
            choices = asyncio.run(_calendar_rsvp_autocomplete(interaction, "team"))
        self.assertEqual([choice.value for choice in choices], ["ev-1"])
        self.assertIn("Teamabend", choices[0].name)


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

    def test_bundled_overview_reads_only_callers_data_and_is_private(self):
        interaction = self.interaction(guild_id=77, user_id=42)
        tickets = [
            {"id": "T-1", "channel_id": "111", "status": "open"},
            {"id": "T-2", "channel_id": "222", "status": "closed"},
        ]
        events = [{"title": "Teamabend", "start_at": "2030-01-01T18:00:00+00:00", "response": "going"}]
        prefs = {"dm": False, "muted": ["tickets", "events"], "quiet_from": 22, "quiet_to": 7}
        with patch("cogs.pulse_pro.db.list_user_tickets", return_value=tickets) as ticket_query, \
             patch("pulse_ultimate.list_user_calendar_rsvps", return_value=events) as event_query, \
             patch("pulse_notify_prefs.load", return_value=prefs) as prefs_load:
            asyncio.run(PulsePro.self_service.callback(PulsePro.__new__(PulsePro), interaction))
        ticket_query.assert_called_once_with(77, 42, limit=10)
        event_query.assert_called_once_with(42, limit=3)
        prefs_load.assert_called_once_with(42)
        sent = interaction.response.send_message.await_args.kwargs
        self.assertTrue(sent["ephemeral"])
        fields = [field.value for field in sent["embed"].fields]
        self.assertIn("1 offen", fields[0])
        self.assertIn("<#111>", fields[0])
        self.assertNotIn("<#222>", fields[0])
        self.assertIn("Teamabend", fields[1])
        self.assertIn("22:00–07:00", fields[2])
        self.assertIn("2 Bereiche stumm", fields[2])

    def test_bundled_overview_survives_a_failing_part(self):
        interaction = self.interaction()
        with patch("cogs.pulse_pro.db.list_user_tickets", side_effect=RuntimeError("db")), \
             patch("cogs.pulse_pro.record_error"), \
             patch("pulse_ultimate.list_user_calendar_rsvps", return_value=[]), \
             patch("pulse_notify_prefs.load", return_value={}):
            asyncio.run(PulsePro.self_service.callback(PulsePro.__new__(PulsePro), interaction))
        fields = [field.value for field in interaction.response.send_message.await_args.kwargs["embed"].fields]
        self.assertEqual(fields[0], "Gerade nicht verfügbar.")
        self.assertEqual(fields[1], "Keine kommenden Rückmeldungen.")

    def test_self_service_commands_do_not_query_personal_data_outside_a_guild(self):
        interaction = self.interaction(guild_id=None)
        with patch("cogs.pulse_pro.db.list_user_tickets") as query:
            asyncio.run(PulsePro.my_tickets.callback(PulsePro.__new__(PulsePro), interaction))
        query.assert_not_called()
        sent = interaction.response.send_message.await_args.kwargs
        self.assertTrue(sent["ephemeral"])


if __name__ == "__main__":
    unittest.main()
