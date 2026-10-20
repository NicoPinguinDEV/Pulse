"""K-07: buttons of open cases and office requests still have handlers after a restart."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import discord

from cogs import buero_system, pulse_suite


class FakeResponse:
    def __init__(self):
        self.messages = []

    def is_done(self):
        return bool(self.messages)

    async def send_message(self, text, ephemeral=False):
        self.messages.append((text, ephemeral))


class SuiteTicketTests(unittest.TestCase):
    def test_new_buttons_carry_case_id_and_are_restored_from_custom_id(self):
        view = pulse_suite.TicketView("ticket_abc123")
        ids = [child.custom_id for child in view.children]
        self.assertEqual(ids, ["pulse:suite-ticket:claim:ticket_abc123", "pulse:suite-ticket:close:ticket_abc123",
                               "pulse:suite-ticket:transfer:ticket_abc123"])
        self.assertTrue(view.is_persistent())
        # simulated restart: discord.py matches the template and rebuilds the item
        match = pulse_suite.SuiteTicketButton.__discord_ui_compiled_template__.fullmatch(ids[1])
        item = asyncio.run(pulse_suite.SuiteTicketButton.from_custom_id(None, None, match))
        self.assertEqual((item.action, item.tid), ("close", "ticket_abc123"))

    def test_cog_registers_dynamic_items(self):
        registered = []
        bot = SimpleNamespace(add_dynamic_items=lambda *items: registered.extend(items))
        cog = object.__new__(pulse_suite.PulseSuite)
        cog.bot = bot
        asyncio.run(cog.cog_load())
        self.assertIn(pulse_suite.SuiteTicketButton, registered)

    def test_old_messages_with_random_custom_ids_are_recognised(self):
        old_id = "a" * 32
        message = SimpleNamespace(
            embeds=[SimpleNamespace(title="🎫 Fall ticket_old999")],
            components=[SimpleNamespace(children=[SimpleNamespace(custom_id="b" * 32, label="Ticket übernehmen"),
                                                  SimpleNamespace(custom_id=old_id, label="Schließen")])])
        self.assertEqual(pulse_suite.legacy_suite_ticket_action(message, old_id), ("close", "ticket_old999"))
        self.assertIsNone(pulse_suite.legacy_suite_ticket_action(message, "pulse:suite-ticket:close:x"))
        other = SimpleNamespace(embeds=[SimpleNamespace(title="Etwas anderes")], components=message.components)
        self.assertIsNone(pulse_suite.legacy_suite_ticket_action(other, old_id))


class BueroTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(buero_system, "DB_NAME", str(Path(self.tmp.name) / "buero.db"))
        self.db.start()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def test_cog_registers_stateless_view_with_old_custom_id(self):
        views = []
        bot = SimpleNamespace(add_view=views.append)
        cog = object.__new__(buero_system.BueroSystemCog)
        cog.bot = bot
        asyncio.run(cog.cog_load())
        self.assertEqual([c.custom_id for c in views[0].children], ["buero_uebernehmen_btn", "buero_auftrag_erledigt_btn"])
        self.assertTrue(views[0].is_persistent())

    def test_stateless_view_restores_request_from_db(self):
        buero_system.save_pending_request(77, 1, 5, 9001, 0)
        self.assertEqual(buero_system.get_pending_request_by_message(9001)["user_id"], 77)
        view = buero_system.BueroUebernehmenView(SimpleNamespace(), 0, 0)
        # unknown message → friendly hint, nothing else happens
        interaction = SimpleNamespace(guild=SimpleNamespace(get_member=lambda _: None), message=SimpleNamespace(id=1234),
                                      response=FakeResponse(), user=SimpleNamespace())
        asyncio.run(view.uebernehmen.callback(interaction))
        self.assertIn("nicht mehr aktiv", interaction.response.messages[0][0])
        self.assertEqual(view.waiting_user_id, 0)  # shared persistent view keeps no per-request state


if __name__ == "__main__":
    unittest.main()
