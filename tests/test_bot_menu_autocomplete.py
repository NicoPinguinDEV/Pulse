"""Audit #4/#6: Autovervollständigung für Aufgaben und Fallreferenzen, persönliches Aktionsmenü /menue (temp DB, keine Discord-Verbindung)."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_ack
import pulse_case_registry as registry
import pulse_db
import pulse_menu
import pulse_player_reports as reports
import webserver

LEAD = {"can_view_dashboard": True, "can_promote": True, "can_manage_tickets": True, "can_view_analytics": True, "can_moderate_kicks": True}
TEAM = {"can_view_dashboard": True}
ME = "900000000000000001"


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        for p in (patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")), patch.object(reports, "DB_PATH", str(Path(self.tmp.name) / "pulse_suite.db"))):
            p.start()
            self.addCleanup(p.stop)
        pulse_db.init_db()


class RecentRefTests(Base):
    def setUp(self):
        super().setUp()
        self.ticket = pulse_db.create_ticket("c1", "100", "900000000000000009", "Spieler", "Support", "high")
        self.tid = self.ticket["id"] if isinstance(self.ticket, dict) else self.ticket
        self.report = reports.create_report(100, 900000000000000002, "Reporter", "Ziel Spieler", "cheating", "Eine ausreichend lange Beschreibung des Vorfalls.", "normal", None)

    def test_only_permitted_kinds_are_offered(self):
        refs = [r for r, _ in registry.recent_refs(LEAD, guild_id=100)]
        self.assertIn(f"TK:{self.tid}", refs)
        self.assertIn(self.report["id"].join(["SM:", ""]), refs)
        only_tickets = [r for r, _ in registry.recent_refs({"can_manage_tickets": True}, guild_id=100)]
        self.assertEqual(only_tickets, [f"TK:{self.tid}"])
        self.assertEqual(registry.recent_refs(TEAM, guild_id=100), [])
        self.assertEqual(registry.recent_refs(None, guild_id=100), [])

    def test_guild_separation_query_and_limit(self):
        self.assertEqual(registry.recent_refs(LEAD, guild_id=999), [])
        found = registry.recent_refs(LEAD, "ziel", guild_id=100)
        self.assertEqual([r for r, _ in found], [self.report["id"].join(["SM:", ""])])
        self.assertEqual(registry.recent_refs(LEAD, "gibtesnicht", guild_id=100), [])
        self.assertLessEqual(len(registry.recent_refs(LEAD, guild_id=100, limit=1)), 1)

    def test_a_failing_source_is_skipped(self):
        with patch("pulse_player_reports.list_reports", side_effect=RuntimeError("x")):
            refs = [r for r, _ in registry.recent_refs(LEAD, guild_id=100)]
        self.assertEqual(refs, [f"TK:{self.tid}"])


class AutocompleteTests(Base):
    def test_task_autocomplete_lists_only_own_open_tasks(self):
        from cogs.pulse_pro import PulsePro
        mine = pulse_db.create_task("Akte prüfen", "", ME, "Ann", "9", "Chef")
        done = pulse_db.create_task("Schon erledigt", "", ME, "Ann", "9", "Chef")
        pulse_db.update_task(done, status="done", actor_id="9", actor_name="Chef", note="x")
        pulse_db.create_task("Fremde Aufgabe", "", "900000000000000002", "Bo", "9", "Chef")
        inter = SimpleNamespace(user=SimpleNamespace(id=int(ME)))
        choices = asyncio.run(PulsePro._task_autocomplete(SimpleNamespace(), inter, ""))
        self.assertEqual([c.value for c in choices], [mine])
        self.assertEqual(asyncio.run(PulsePro._task_autocomplete(SimpleNamespace(), inter, "akte"))[0].value, mine)
        self.assertEqual(asyncio.run(PulsePro._task_autocomplete(SimpleNamespace(), inter, "gibtesnicht")), [])

    def test_reference_autocomplete_uses_the_callers_rights(self):
        from cogs import mod_cases
        ticket = pulse_db.create_ticket("c2", "100", "900000000000000009", "Spieler", "Support", "normal")
        tid = ticket["id"] if isinstance(ticket, dict) else ticket
        inter = SimpleNamespace(guild=SimpleNamespace(id=100), user=SimpleNamespace(id=int(ME)))
        with patch.object(mod_cases, "pulse_perms", return_value={"can_manage_tickets": True}):
            choices = asyncio.run(mod_cases.ModCases._ref_autocomplete(SimpleNamespace(), inter, ""))
        self.assertEqual([c.value for c in choices], [f"TK:{tid}"])
        with patch.object(mod_cases, "pulse_perms", return_value={}):
            self.assertEqual(asyncio.run(mod_cases.ModCases._ref_autocomplete(SimpleNamespace(), inter, "")), [])
        self.assertEqual(asyncio.run(mod_cases.ModCases._ref_autocomplete(SimpleNamespace(), SimpleNamespace(guild=None, user=inter.user), "")), [])

    def test_commands_are_wired_to_the_autocomplete(self):
        from cogs.mod_cases import ModCases
        from cogs.pulse_pro import PulsePro
        for command, name in ((ModCases.fall, "referenz"), (ModCases.beweise, "referenz"), (ModCases.beweis_hinzufuegen, "referenz"), (PulsePro.taskdone, "task_id")):
            self.assertIn(name, command._params)
            self.assertIsNotNone(command._params[name].autocomplete, f"{command.name}.{name}")


class MenuTests(Base):
    def test_sections_show_counts_commands_and_links(self):
        pulse_db.create_task("A", "", ME, "Ann", "9", "Chef")
        pulse_ack.require("announcement", "a1", "Regel")
        data = pulse_menu.section("aufgaben", ME, LEAD, "https://pulse.example/")
        text = "\n".join(data["lines"])
        self.assertIn("Offene Aufgaben: **1**", text)
        self.assertIn("Offene Bestätigungen: **1**", text)
        self.assertIn("`/teamtask`", text)
        self.assertIn(("Aufgaben", "https://pulse.example/tasks"), data["links"])
        self.assertNotIn("`/teamtask`", "\n".join(pulse_menu.section("aufgaben", ME, TEAM)["lines"]))

    def test_links_only_with_a_base_url_and_unknown_sections_are_none(self):
        self.assertEqual(pulse_menu.section("wissen", ME, TEAM, "")["links"], [])
        self.assertIsNone(pulse_menu.section("gibtesnicht", ME, TEAM))
        for key in pulse_menu.SECTIONS:
            self.assertTrue(pulse_menu.section(key, ME, LEAD, "https://x.example")["lines"], key)

    def test_ticket_and_event_sections_follow_the_rights(self):
        member = "\n".join(pulse_menu.section("tickets", ME, TEAM)["lines"])
        lead = "\n".join(pulse_menu.section("tickets", ME, LEAD)["lines"])
        self.assertNotIn("Von dir übernommen", member)
        self.assertIn("Von dir übernommen", lead)
        self.assertIn("`/umfrage`", "\n".join(pulse_menu.section("termine", ME, LEAD)["lines"]))
        self.assertNotIn("`/umfrage`", "\n".join(pulse_menu.section("termine", ME, TEAM)["lines"]))

    def test_missing_data_never_blocks_the_menu(self):
        with patch.object(pulse_db, "list_tasks", side_effect=RuntimeError("x")):
            data = pulse_menu.section("aufgaben", ME, TEAM)
        self.assertIn("**?**", data["lines"][0])

    def run_menu(self, perms, member=True):
        from cogs.pulse_pro import PulsePro
        inter = SimpleNamespace(guild=SimpleNamespace(id=1), user=SimpleNamespace(id=int(ME)), response=SimpleNamespace(send_message=AsyncMock(), edit_message=AsyncMock()))
        with patch.object(webserver, "compute_perms", return_value=(perms, object() if member else None)), patch.object(webserver, "load_config", return_value={}), \
                patch("cogs.pulse_pro.PUBLIC_BASE_URL", "https://pulse.example"):
            asyncio.run(PulsePro.menue.callback(SimpleNamespace(), inter))
        return inter

    def test_command_shows_the_menu_privately_and_switches_sections(self):
        inter = self.run_menu(LEAD)
        args, kwargs = inter.response.send_message.await_args
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("Aufgaben", kwargs["embed"].title)
        view = kwargs["view"]
        select = view.children[0]
        self.assertEqual({o.value for o in select.options}, set(pulse_menu.SECTIONS))
        select._values = ["termine"]
        switch = SimpleNamespace(user=SimpleNamespace(id=int(ME)), response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()))
        asyncio.run(select.callback(switch))
        self.assertIn("Termine", switch.response.edit_message.await_args.kwargs["embed"].title)
        stranger = SimpleNamespace(user=SimpleNamespace(id=1), response=SimpleNamespace(edit_message=AsyncMock(), send_message=AsyncMock()))
        asyncio.run(select.callback(stranger))
        stranger.response.edit_message.assert_not_awaited()
        self.assertIn("anderen Person", stranger.response.send_message.await_args.args[0])

    def test_non_team_members_get_no_menu(self):
        inter = self.run_menu(TEAM, member=False)
        self.assertIn("Kein Teamzugriff", inter.response.send_message.await_args.args[0])


if __name__ == "__main__":
    unittest.main()
