"""Audit #57: zusätzliche Widgets (Bestätigungen, Abstimmungen, Besprechungen) auf „Mein Pulse“ (temp DB, keine Discord-Verbindung)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_ack
import pulse_db
import pulse_home
import pulse_home_widgets as hw
import pulse_meetings
import pulse_poll_cards as polls
import pulse_ultimate as pu

ME, OTHER = "900000000000000001", "900000000000000002"


def card(title, body, link=""):
    return f"<card>{title}|{body}|{link}</card>"


def esc(value):
    return str(value).replace("<", "&lt;").replace(">", "&gt;")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")), patch.object(pu, "_INIT", False)):
            p.start()
            self.addCleanup(p.stop)
        pulse_db.init_db()
        pu.setup()


class CatalogTests(unittest.TestCase):
    def test_new_keys_are_registered_and_normalized(self):
        keys = [key for key, _label in pulse_home.HOME_WIDGETS]
        for key in ("ack", "polls", "besprechungen"):
            self.assertIn(key, keys)
            self.assertIn(key, pulse_home.normalize_home_widgets(None))
        self.assertEqual(pulse_home.normalize_home_widgets(["besprechungen", "week", "x"]), ["week", "besprechungen"])
        self.assertEqual(set(hw.WIDGETS), {"ack", "polls", "besprechungen"})


class WidgetTests(Base):
    def test_ack_widget_lists_pending_and_empty_state(self):
        self.assertIn("Nichts zu bestätigen", hw.ack_widget(ME, card, esc))
        pulse_ack.require("announcement", "a1", "Regel <b>neu</b>")
        html = hw.ack_widget(ME, card, esc)
        self.assertIn("Regel &lt;b&gt;neu&lt;/b&gt;", html)
        self.assertNotIn("<b>neu</b>", html)
        pulse_ack.acknowledge("announcement", "a1", ME)
        self.assertIn("Nichts zu bestätigen", hw.ack_widget(ME, card, esc))
        self.assertIn("Regel", hw.ack_widget(OTHER, card, esc))        # jede Person sieht nur ihren eigenen Stand

    def test_ack_widget_is_bounded(self):
        for i in range(8):
            pulse_ack.require("announcement", f"a{i}", f"Titel {i}")
        html = hw.ack_widget(ME, card, esc)
        self.assertEqual(html.count("pulse-row-title"), 5)
        self.assertIn("+3 weitere", html)

    def test_polls_widget_shows_only_polls_without_my_vote(self):
        poll = polls.create("Wann treffen wir uns?", "Mo | Di", None, OTHER, "Bo")
        self.assertIn("Wann treffen wir uns?", hw.polls_widget(ME, card, esc))
        polls.vote(poll["id"], ME, 0)
        self.assertIn("Keine offene Abstimmung", hw.polls_widget(ME, card, esc))
        self.assertIn("Wann treffen wir uns?", hw.polls_widget("900000000000000003", card, esc))

    def test_ended_polls_are_not_shown(self):
        poll = polls.create("Alt?", "A | B", 1, OTHER, "Bo")
        with pu.cx() as c:
            c.execute("UPDATE ultimate_polls SET ends_at='2000-01-01T00:00:00+00:00' WHERE id=?", (poll["id"],))
        self.assertIn("Keine offene Abstimmung", hw.polls_widget(ME, card, esc))

    def test_meetings_widget_shows_planned_and_running_only(self):
        self.assertIn("Keine anstehende Besprechung", hw.meetings_widget(ME, card, esc))
        planned = pulse_meetings.create("Teamsitzung", "2026-11-20T18:00", ["Lage"], [(ME, "Ann")])
        done = pulse_meetings.create("Alt", "2026-01-01T18:00", ["Lage"], [(ME, "Ann")])
        pulse_meetings.start(done["id"])
        pulse_meetings.close(done["id"])
        html = hw.meetings_widget(ME, card, esc)
        self.assertIn("Teamsitzung", html)
        self.assertIn(f"/besprechungen/{planned['id']}", html)
        self.assertNotIn(">Alt<", html)

    def test_cards_drops_only_the_failing_widget(self):
        with patch.object(pulse_ack, "pending_for", side_effect=RuntimeError("x")):
            built = hw.cards(ME, card, esc)
        self.assertEqual(set(built), {"polls", "besprechungen"})
        self.assertEqual(set(hw.cards(ME, card, esc)), {"ack", "polls", "besprechungen"})


if __name__ == "__main__":
    unittest.main()
