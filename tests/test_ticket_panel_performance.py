"""Ticket panel workload rendering performs one grouped count query per render."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import discord

from cogs import tickets


class TicketPanelWorkloadTests(unittest.TestCase):
    def setUp(self):
        self.definitions = {
            "support": {"id": "support", "name": "Support", "description": "Hilfe", "emoji": "🎫", "max_open": 10,
                        "group": "team", "active": True},
            "bug": {"id": "bug", "name": "Bug", "description": "Fehler", "emoji": "⚙️", "max_open": 5,
                    "group": "team", "active": True},
            "hidden": {"id": "hidden", "name": "Hidden", "description": "", "emoji": "🔒", "max_open": 1,
                       "group": "team", "active": False},
        }

    def test_panel_entries_fetches_category_counts_once_and_reuses_definitions(self):
        panel = {"category_keys": ["support", "bug", "hidden"], "show_load": True}
        with (
            patch.object(tickets, "ticket_category_definitions", return_value=self.definitions) as definitions,
            patch.object(tickets._pulse_db, "open_ticket_category_counts", return_value={"support": 4, "bug": 2}) as counts,
            patch.object(tickets, "category_meta", side_effect=AssertionError("must not reload definitions per category")),
        ):
            entries = tickets.panel_entries(panel)

        self.assertEqual(entries, [("Support", "🎫", 4, 10), ("Bug", "⚙️", 2, 5)])
        definitions.assert_called_once_with()
        counts.assert_called_once_with(["support", "bug"])

    def test_modern_panel_layout_reuses_entries_for_embed_and_layout(self):
        panel_config = {"category_keys": ["support"], "show_load": True, "list_categories": True}
        panel = {"category_keys": ["support"], "show_load": True, "list_categories": True}
        entries = [("Support", "🎫", 3, 10)]
        embed = discord.Embed(title="Ticket-Support")
        with (
            patch.object(tickets, "normalize_panel", return_value=panel),
            patch.object(tickets, "panel_entries", return_value=entries) as panel_entries,
            patch.object(tickets, "build_panel_embed", return_value=embed) as build_embed,
            patch.object(tickets, "embed_to_spec", return_value={"fields": []}),
            patch.object(tickets, "category_block", return_value={"type": "block"}) as block,
            patch.object(tickets, "layout_from_spec", return_value="layout") as layout,
            patch.object(tickets, "view_rows", return_value=[]),
        ):
            result = tickets.build_panel_layout(SimpleNamespace(), panel_config, SimpleNamespace())

        self.assertEqual(result, "layout")
        panel_entries.assert_called_once_with(panel)
        build_embed.assert_called_once_with(SimpleNamespace(), panel_config, None, entries=entries)
        self.assertEqual(block.call_args.args[0], entries)
        layout.assert_called_once()

    def test_ticket_select_batches_counts_and_reuses_category_definitions(self):
        panel = {
            "category_keys": ["support", "bug", "hidden"],
            "show_load": True,
            "component_style": "select",
        }
        with (
            patch.object(tickets, "ticket_category_definitions", return_value=self.definitions) as definitions,
            patch.object(
                tickets._pulse_db, "open_ticket_category_counts",
                return_value={"support": 4, "bug": 2},
            ) as counts,
        ):
            view = tickets.panel_view(panel)

        definitions.assert_called_once_with()
        counts.assert_called_once_with(["support", "bug"])
        select = view.children[0]
        self.assertEqual([option.value for option in select.options], ["support", "bug"])
        self.assertIn("4/10 offen", select.options[0].description)
        self.assertEqual(
            view._pulse_panel_entries,
            [("Support", "🎫", 4, 10), ("Bug", "⚙️", 2, 5)],
        )

    def test_legacy_panel_uses_one_grouped_category_count_query(self):
        guild = SimpleNamespace(icon=None)
        with patch.object(
            tickets._pulse_db, "open_ticket_category_counts",
            return_value={key: 1 for key in tickets.CATEGORY_META},
        ) as counts:
            embed = tickets.build_panel_embed(guild)

        self.assertIsInstance(embed, discord.Embed)
        counts.assert_called_once_with(tickets.CATEGORY_META)


if __name__ == "__main__":
    unittest.main()
