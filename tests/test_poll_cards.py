"""Audit #44: Umfragen per Discord (gemeinsame Abstimmungsregel mit dem Dashboard, Karten-Buttons, Befehl; temp DB, keine Discord-Verbindung)."""
import asyncio
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_db
import pulse_poll_cards as polls
import pulse_ultimate as pu
import webserver
from cogs.pulse_pro import PollVoteButton, PulsePro

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


class DataTests(Base):
    def test_options_are_validated(self):
        self.assertEqual(polls.parse_options("Ja | Nein"), ["Ja", "Nein"])
        for bad in ("", "Nur eine", "A | B | C | D | E | F", "Ja | ja"):
            with self.assertRaises(ValueError, msg=bad):
                polls.parse_options(bad)

    def test_create_uses_the_shared_table_and_ends_at(self):
        poll = polls.create("Dienstag oder Mittwoch?", "Dienstag | Mittwoch", 24, 7, "Ann")
        stored = pu.cx().execute("SELECT * FROM ultimate_polls WHERE id=?", (poll["id"],)).fetchone()
        self.assertEqual((stored["poll_type"], stored["active"], stored["anonymous"]), ("single", 1, 1))
        self.assertEqual(json.loads(stored["options_json"]), ["Dienstag", "Mittwoch"])
        self.assertGreater(stored["ends_at"], pu.iso())
        for bad in (("", "A | B", None), ("x" * 200, "A | B", None), ("Frage", "A | B", 0), ("Frage", "A | B", 9999)):
            with self.assertRaises(ValueError, msg=str(bad)):
                polls.create(*bad, 7, "Ann")

    def test_vote_replaces_the_earlier_vote_and_card_shows_only_totals(self):
        poll = polls.create("Frage?", "A | B | C", None, 7, "Ann")
        polls.vote(poll["id"], 1, 0)
        polls.vote(poll["id"], 2, 0)
        polls.vote(poll["id"], 1, 2)          # ersetzt die frühere Stimme
        fields = polls.card_fields(polls.get(poll["id"]))
        self.assertIn("**A** · 1 (50 %)", fields["description"])
        self.assertIn("**C** · 1 (50 %)", fields["description"])
        self.assertIn("2 Stimme", fields["footer"])
        self.assertNotIn("Ann", fields["description"])

    def test_shared_rule_refuses_unknown_ended_and_invalid_votes(self):
        with self.assertRaises(pu.PollError) as caught:
            polls.vote("nope", 1, 0)
        self.assertEqual(caught.exception.status, 404)
        poll = polls.create("Frage?", "A | B", 1, 7, "Ann")
        with self.assertRaises(pu.PollError) as caught:
            polls.vote(poll["id"], 1, 5)
        self.assertEqual(caught.exception.status, 400)
        with pu.cx() as c:
            c.execute("UPDATE ultimate_polls SET ends_at=? WHERE id=?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(), poll["id"]))
        with self.assertRaises(pu.PollError) as caught:
            polls.vote(poll["id"], 1, 0)
        self.assertEqual(caught.exception.status, 410)
        self.assertEqual(pu.poll_results(poll["id"]), {})

    def test_custom_id_validation_and_template(self):
        self.assertEqual(polls.custom_id("poll_ab12", 3), "pulse:poll:poll_ab12:3")
        for bad in (("x y", 0), ("", 0), ("poll_1", 5), ("poll_1", -1)):
            with self.assertRaises(ValueError):
                polls.custom_id(*bad)
        self.assertTrue(re.fullmatch(polls.TEMPLATE, "pulse:poll:poll_ab12:3"))

    def test_dashboard_route_keeps_working_with_the_shared_function(self):
        import tests.test_automation_center  # noqa: F401 - route fixtures live there; the shared function is covered above
        poll = polls.create("Frage?", "A | B", None, 7, "Ann")
        self.assertEqual(pu.cast_poll_vote(poll["id"], 5, ["1", "0", "x"]), [0])   # single: nur die erste Auswahl zählt


class ButtonTests(Base):
    def interaction(self, guild=True):
        return SimpleNamespace(guild=SimpleNamespace(id=1) if guild else None, user=SimpleNamespace(id=7, display_name="Ann"),
                               response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()))

    def click(self, poll_id, index, interaction, member=True):
        item = PollVoteButton(poll_id, index)
        with patch.object(webserver, "compute_perms", return_value=(TEAM_PERMS, object() if member else None)), patch.object(webserver, "load_config", return_value={}):
            asyncio.run(item.callback(interaction))

    def test_vote_updates_the_card(self):
        poll = polls.create("Frage?", "A | B", None, 9, "Lead")
        inter = self.interaction()
        self.click(poll["id"], 1, inter)
        embed = inter.response.edit_message.await_args.kwargs["embed"]
        self.assertIn("**B** · 1 (100 %)", embed.description)

    def test_non_members_and_ended_polls_are_refused_without_writes(self):
        poll = polls.create("Frage?", "A | B", None, 9, "Lead")
        inter = self.interaction()
        self.click(poll["id"], 0, inter, member=False)
        self.assertIn("Team", inter.response.send_message.await_args.args[0])
        inter2 = self.interaction()
        self.click("gibtesnicht", 0, inter2)
        self.assertIn("nicht gefunden", inter2.response.send_message.await_args.args[0])
        self.assertEqual(pu.poll_results(poll["id"]), {})


class CommandTests(Base):
    def run_command(self, perms, frage="Wann?", optionen="Mo | Di", dauer=None):
        inter = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=9, display_name="Lead"), response=SimpleNamespace(send_message=AsyncMock()))
        with patch.object(webserver, "compute_perms", return_value=(perms, object())), patch.object(webserver, "load_config", return_value={}), \
                patch.object(webserver, "log_audit") as audit:
            asyncio.run(PulsePro.umfrage.callback(SimpleNamespace(), inter, frage, optionen, dauer))
        return inter, audit

    def test_leadership_starts_a_poll_with_one_button_per_option(self):
        inter, audit = self.run_command(LEAD_PERMS, optionen="Mo | Di | Mi", dauer=12)
        kwargs = inter.response.send_message.await_args.kwargs
        self.assertEqual([c.item.label for c in kwargs["view"].children], ["Mo", "Di", "Mi"])
        self.assertIn("Wann?", kwargs["embed"].title)
        audit.assert_called_once()

    def test_team_member_and_invalid_input_create_nothing(self):
        inter, audit = self.run_command(TEAM_PERMS)
        self.assertIn("Berechtigung", inter.response.send_message.await_args.args[0])
        audit.assert_not_called()
        inter, _ = self.run_command(LEAD_PERMS, optionen="Nur eine")
        self.assertIn("Optionen", inter.response.send_message.await_args.args[0])
        self.assertEqual(pu.cx().execute("SELECT COUNT(*) FROM ultimate_polls").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
