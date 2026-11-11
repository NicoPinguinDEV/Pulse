"""Event #42: RSVP updates are per user, idempotent and scoped to valid calendar events."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_ultimate
import webserver


class UltimateEventRsvpTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
            patch.object(pulse_ultimate, "_INIT", False),
        ]
        for item in self.patches:
            item.start()
        pulse_db.init_db()
        pulse_ultimate.setup()
        self.client = TestClient(webserver.app, raise_server_exceptions=False)
        self.member = SimpleNamespace(
            user={"id": "123", "global_name": "Teammitglied", "username": "teammitglied"},
            perms={"can_view_dashboard": True},
        )
        self.manager = SimpleNamespace(
            user={"id": "999", "global_name": "Leitung", "username": "leitung"},
            perms={"can_view_dashboard": True, "can_promote": True},
        )

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def create_event(self, event_id="cal_test", kind="event"):
        with pulse_ultimate.cx() as connection:
            connection.execute(
                "INSERT INTO ultimate_calendar VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id, "Teamabend", kind, "2030-01-01T18:00:00+00:00", None,
                    "Gemeinsamer Termin", "Discord", None, "999", "Leitung", "2029-12-01T12:00:00+00:00",
                ),
            )

    def responses(self, event_id="cal_test"):
        with pulse_ultimate.cx() as connection:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM ultimate_calendar_rsvps WHERE event_id=? ORDER BY user_id",
                    (event_id,),
                ).fetchall()
            ]

    def create_reminder_event(self, event_id, title, starts_at, user_id="123", response="going", kind="event"):
        with pulse_ultimate.cx() as connection:
            connection.execute(
                "INSERT INTO ultimate_calendar VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    event_id, title, kind, starts_at.isoformat(timespec="seconds"), None,
                    "Testort", "Discord", None, "999", "Leitung", "2029-12-01T12:00:00+00:00",
                ),
            )
            connection.execute(
                """INSERT INTO ultimate_calendar_rsvps
                   (event_id,user_id,display_name,response,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                (
                    event_id, str(user_id), f"Nutzer {user_id}", response,
                    "2029-12-01T12:00:00+00:00", "2029-12-01T12:00:00+00:00",
                ),
            )

    def test_going_and_declined_are_saved_and_repeated_rsvp_updates_one_row(self):
        self.create_event()
        with patch.object(pulse_ultimate, "ctx_auth", return_value=self.member), patch.object(pulse_ultimate, "audit"):
            going = self.client.post(
                "/ultimate/calendar/cal_test/rsvp", data={"response": "going"}, follow_redirects=False
            )
            self.assertEqual(going.status_code, 303)
            self.assertEqual(self.responses()[0]["response"], "going")
            self.assertEqual(self.responses()[0]["display_name"], "Teammitglied")

            declined = self.client.post(
                "/ultimate/calendar/cal_test/rsvp", data={"response": "declined"}, follow_redirects=False
            )
            self.assertEqual(declined.status_code, 303)
            rows = self.responses()
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["response"], "declined")

    def test_rsvp_is_scoped_to_the_authenticated_user_and_can_be_removed(self):
        self.create_event()
        other = SimpleNamespace(
            user={"id": "456", "global_name": "Weitere Person", "username": "weitere"},
            perms={"can_view_dashboard": True},
        )
        with patch.object(pulse_ultimate, "ctx_auth", return_value=self.member), patch.object(pulse_ultimate, "audit"):
            self.client.post("/ultimate/calendar/cal_test/rsvp", data={"response": "going"})
        with patch.object(pulse_ultimate, "ctx_auth", return_value=other), patch.object(pulse_ultimate, "audit"):
            self.client.post("/ultimate/calendar/cal_test/rsvp", data={"response": "declined"})
        self.assertEqual({row["user_id"] for row in self.responses()}, {"123", "456"})

        with patch.object(pulse_ultimate, "ctx_auth", return_value=self.member), patch.object(pulse_ultimate, "audit"):
            removed = self.client.post(
                "/ultimate/calendar/cal_test/rsvp", data={"response": "remove"}, follow_redirects=False
            )
        self.assertEqual(removed.status_code, 303)
        self.assertEqual([(row["user_id"], row["response"]) for row in self.responses()], [("456", "declined")])

    def test_deadlines_invalid_responses_and_unknown_events_are_rejected(self):
        self.create_event(kind="deadline")
        with patch.object(pulse_ultimate, "ctx_auth", return_value=self.member), patch.object(pulse_ultimate, "audit"):
            self.assertEqual(
                self.client.post("/ultimate/calendar/cal_test/rsvp", data={"response": "going"}).status_code,
                400,
            )
            self.assertEqual(
                self.client.post("/ultimate/calendar/missing/rsvp", data={"response": "going"}).status_code,
                404,
            )
            self.assertEqual(
                self.client.post("/ultimate/calendar/cal_test/rsvp", data={"response": "maybe"}).status_code,
                400,
            )
        self.assertEqual(self.responses(), [])

    def test_calendar_shows_aggregated_and_named_participants(self):
        self.create_event()
        with pulse_ultimate.cx() as connection:
            connection.executemany(
                """INSERT INTO ultimate_calendar_rsvps
                   (event_id,user_id,display_name,response,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                [
                    ("cal_test", "123", "Teammitglied", "going", "2029-12-01", "2029-12-01"),
                    ("cal_test", "456", "Weitere Person", "declined", "2029-12-01", "2029-12-01"),
                ],
            )
        with (
            patch.object(pulse_ultimate, "ctx_auth", return_value=self.member),
            patch.object(pulse_ultimate, "page", side_effect=lambda _ctx, _nav, _title, _subtitle, body: body),
        ):
            response = self.client.get("/ultimate/calendar")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Teilnehmerliste", response.text)
        self.assertIn("Teammitglied", response.text)
        self.assertIn("Weitere Person", response.text)
        self.assertIn("1 Zusagen", response.text)
        self.assertIn("1 Absagen", response.text)
        self.assertIn("/ultimate/calendar/cal_test/rsvp", response.text)

    def test_calendar_reminder_candidates_are_scoped_to_going_rsvps_and_24_hour_window(self):
        now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.create_reminder_event("within-window", "Teamabend", now + timedelta(hours=20), "123", "going")
        self.create_reminder_event("too-far", "Später Termin", now + timedelta(hours=25), "123", "going")
        self.create_reminder_event("declined", "Abgesagt", now + timedelta(hours=2), "456", "declined")
        self.create_reminder_event("past", "Vergangen", now - timedelta(minutes=1), "789", "going")
        self.create_reminder_event("deadline", "Abgabefrist", now + timedelta(hours=2), "999", "going", "deadline")

        candidates = pulse_ultimate.calendar_reminder_candidates(now=now, lead_hours=24)

        self.assertEqual(
            [(row["event_id"], row["user_id"]) for row in candidates],
            [("within-window", "123")],
        )

    def test_calendar_reminders_create_one_deduplicated_inbox_notice_for_each_confirmed_rsvp(self):
        now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.create_reminder_event("reminder-1", "Teamabend", now + timedelta(hours=20), "123", "going")
        self.create_reminder_event("reminder-declined", "Nicht dabei", now + timedelta(hours=20), "456", "declined")

        first = pulse_ultimate.send_calendar_reminders(now=now)
        second = pulse_ultimate.send_calendar_reminders(now=now)

        own = pulse_db.notifications("123")
        other = pulse_db.notifications("456")
        self.assertEqual(first, 1)
        self.assertEqual(second, 1)  # notify deduplication prevents a second inbox row
        self.assertEqual(len(own), 1)
        self.assertIn("Erinnerung: Teamabend", own[0]["title"])
        self.assertIn("01.01.2030", own[0]["body"])
        self.assertEqual(other, [])
        self.assertEqual(own[0]["url"], "/ultimate/calendar")

    def test_calendar_reminders_use_only_the_existing_notification_settings(self):
        now = datetime(2030, 1, 1, 12, 0, tzinfo=timezone.utc)
        self.create_reminder_event("reminder-muted", "Teamabend", now + timedelta(hours=20), "123", "going")
        import pulse_notify_prefs

        pulse_notify_prefs.save("123", {"muted": ["events"], "dm": False})
        pulse_ultimate.send_calendar_reminders(now=now)

        notifications = pulse_db.notifications("123")
        self.assertEqual(len(notifications), 1)
        self.assertIsNotNone(notifications[0]["read_at"])  # muted category remains in inbox, already read
        self.assertEqual(pulse_notify_prefs.category_for(notifications[0]["url"], notifications[0]["title"]), "events")

    def test_deleting_an_event_cascades_its_rsvp_rows(self):
        self.create_event()
        with pulse_ultimate.cx() as connection:
            connection.execute(
                """INSERT INTO ultimate_calendar_rsvps
                   (event_id,user_id,display_name,response,created_at,updated_at)
                   VALUES(?,?,?,?,?,?)""",
                ("cal_test", "123", "Teammitglied", "going", "2029-12-01", "2029-12-01"),
            )
        with patch.object(pulse_ultimate, "ctx_auth", return_value=self.manager), patch.object(pulse_ultimate, "audit"):
            response = self.client.post("/ultimate/calendar/cal_test/delete", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.responses(), [])


if __name__ == "__main__":
    unittest.main()
