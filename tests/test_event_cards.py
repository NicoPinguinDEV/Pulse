"""Audit #41: Terminkarten mit Zu-/Absage-Buttons (Daten, gemeinsame RSVP-Regel, Buttons, Befehl; temp DB, keine Discord-Verbindung)."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_db
import pulse_event_cards as cards
import pulse_ultimate as pu
import webserver
from cogs.pulse_pro import EventRsvpButton, PulsePro

TEAM_PERMS = {"can_view_dashboard": True}
LEAD_PERMS = {"can_view_dashboard": True, "can_promote": True}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")), patch.object(pu, "_INIT", False)):
            p.start()
            self.addCleanup(p.stop)
        pulse_db.init_db()
        pu.setup()
        future = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat(timespec="seconds")
        past = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat(timespec="seconds")
        with pu.cx() as c:
            for eid, title, kind, start in (("ev1", "Teamsitzung", "meeting", future), ("ev2", "Vergangen", "meeting", past), ("ev3", "Wartung", "maintenance", future)):
                c.execute("INSERT INTO ultimate_calendar(id,title,kind,start_at,end_at,description,location,member_id,created_by_id,created_by_name,created_at) "
                          "VALUES(?,?,?,?,?,?,?,?,?,?,?)", (eid, title, kind, start, None, "Beschreibung", "Discord", None, "1", "Lead", start))


class DataTests(Base):
    def test_custom_id_is_validated(self):
        self.assertEqual(cards.custom_id("ev1", "going"), "pulse:rsvp:ev1:going")
        for bad in (("ev 1", "going"), ("", "going"), ("ev1", "maybe"), ("x" * 70, "going")):
            with self.assertRaises(ValueError):
                cards.custom_id(*bad)

    def test_answers_count_exactly_and_bound_the_names(self):
        for i in range(15):
            cards.respond("ev1", 1000 + i, f"Person {i}", "going")
        cards.respond("ev1", 5, "Nein", "declined")
        data = cards.answers("ev1")
        self.assertEqual((data["counts"], len(data["going"])), ({"going": 15, "declined": 1}, cards.MAX_NAMES))
        fields = cards.card_fields(cards.get_event("ev1"), data)
        self.assertIn("(+3)", dict((n, v) for n, v, _i in fields["fields"])["✅ Zusagen"])
        self.assertIn("Teamsitzung", fields["title"])

    def test_response_can_change_and_be_withdrawn(self):
        cards.respond("ev1", 7, "Ann", "going")
        self.assertEqual(cards.respond("ev1", 7, "Ann", "declined")["counts"], {"going": 0, "declined": 1})
        self.assertEqual(cards.respond("ev1", 7, "Ann", "remove")["counts"], {"going": 0, "declined": 0})

    def test_shared_rule_refuses_past_events_wrong_kinds_and_unknown_answers(self):
        for event_id, response in (("ev2", "going"), ("ev3", "going"), ("nope", "going"), ("ev1", "vielleicht")):
            with self.assertRaises(pu.CalendarRsvpError, msg=event_id):
                cards.respond(event_id, 7, "Ann", response)
        self.assertEqual(cards.answers("ev1")["counts"], {"going": 0, "declined": 0})


class ButtonTests(Base):
    def interaction(self, uid=7, guild=True):
        return SimpleNamespace(guild=SimpleNamespace(id=1) if guild else None, user=SimpleNamespace(id=uid, display_name="Ann"),
                               response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()))

    def click(self, response, interaction, perms=TEAM_PERMS, member=True, event="ev1"):
        item = EventRsvpButton(event, response)
        with patch.object(webserver, "compute_perms", return_value=(perms, object() if member else None)), patch.object(webserver, "load_config", return_value={}):
            asyncio.run(item.callback(interaction))

    def test_team_member_answers_and_the_card_is_updated(self):
        inter = self.interaction()
        self.click("going", inter)
        embed = inter.response.edit_message.await_args.kwargs["embed"]
        self.assertEqual(embed.fields[0].value.split("\n")[0], "1")
        self.assertEqual(cards.answers("ev1")["counts"]["going"], 1)

    def test_non_members_and_dm_clicks_write_nothing(self):
        inter = self.interaction()
        self.click("going", inter, member=False)
        self.assertIn("Team", inter.response.send_message.await_args.args[0])
        inter2 = self.interaction(guild=False)
        self.click("going", inter2)
        self.assertEqual(cards.answers("ev1")["counts"]["going"], 0)

    def test_past_event_answer_is_refused_with_the_message(self):
        inter = self.interaction()
        self.click("going", inter, event="ev2")
        self.assertIn("begonnen", inter.response.send_message.await_args.args[0])

    def test_dynamic_item_template_matches_the_custom_ids(self):
        import re
        for response in cards.RESPONSES:
            self.assertTrue(re.fullmatch(EventRsvpButton.__discord_ui_compiled_template__.pattern, cards.custom_id("ev1", response)))


class CommandTests(Base):
    def run_command(self, perms, event="ev1"):
        inter = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=9, display_name="Lead"), response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(webserver, "compute_perms", return_value=(perms, object())), patch.object(webserver, "load_config", return_value={}), \
                patch.object(webserver, "log_audit") as audit:
            asyncio.run(PulsePro.terminkarte.callback(SimpleNamespace(), inter, event))
        return inter, audit

    def test_leadership_publishes_a_card_with_three_buttons(self):
        inter, audit = self.run_command(LEAD_PERMS)
        kwargs = inter.response.send_message.await_args.kwargs
        self.assertEqual(len(kwargs["view"].children), 3)
        self.assertIn("Teamsitzung", kwargs["embed"].title)
        audit.assert_called_once()

    def test_team_member_cannot_publish_and_other_kinds_are_refused(self):
        inter, audit = self.run_command(TEAM_PERMS)
        self.assertIn("Berechtigung", inter.response.send_message.await_args.args[0])
        audit.assert_not_called()
        inter, _ = self.run_command(LEAD_PERMS, event="ev3")
        self.assertIn("kann keine", inter.response.send_message.await_args.args[0])
        inter, _ = self.run_command(LEAD_PERMS, event="nope")
        self.assertIn("kann keine", inter.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
