"""Phase 3: background tasks (P-07), on_ready once (P-08), command sync only on change (P-09)."""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from discord import app_commands

import pulse_command_sync
import pulse_db
import pulse_tasks
from pulse_ready import first_ready


class SpawnTests(unittest.TestCase):
    def test_reference_kept_and_errors_logged(self):
        async def boom():
            await asyncio.sleep(0)
            raise RuntimeError("kaputt")

        async def scenario():
            task = pulse_tasks.spawn(boom(), name="test-task")
            self.assertIn(task, pulse_tasks._RUNNING)
            await asyncio.sleep(0.01)
            return task
        with patch("builtins.print") as printed, patch("traceback.print_exception"):
            task = asyncio.run(scenario())
        self.assertNotIn(task, pulse_tasks._RUNNING)
        self.assertTrue(any("Fehler-ID" in str(c) and "test-task" in str(c) for c in printed.call_args_list))


class ReadyOnceTests(unittest.TestCase):
    def test_once_and_interval(self):
        bot = SimpleNamespace()
        self.assertTrue(first_ready(bot, "a"))
        self.assertFalse(first_ready(bot, "a"))
        self.assertTrue(first_ready(bot, "b", min_interval=0))
        self.assertTrue(first_ready(bot, "b", min_interval=0))
        self.assertFalse(first_ready(bot, "c", min_interval=600) and first_ready(bot, "c", min_interval=600))

    def test_main_reconciles_warn_roles_only_once(self):
        import main
        calls = []

        async def reconcile(guild, cfg):
            calls.append(1)
            return {"checked": 0, "updated": 0, "failed": 0, "errors": []}
        bot = SimpleNamespace(user=SimpleNamespace(id=1), get_guild=lambda _: None)

        async def presence(**_):
            return None
        bot.change_presence = presence
        with patch("webserver.reconcile_warning_roles", reconcile), patch("builtins.print"):
            asyncio.run(main.CustomBot.on_ready(bot))
            asyncio.run(main.CustomBot.on_ready(bot))  # reconnect
        self.assertEqual(calls, [1])


class CommandSyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def make_tree(self, description):
        import discord
        synced = []

        class Tree(app_commands.CommandTree):
            async def sync(self, *, guild=None):
                synced.append(1)
                return self.get_commands()
        tree = Tree(discord.Client(intents=discord.Intents.none()))  # never connected

        @tree.command(name="ping", description=description)
        async def ping(interaction):
            pass
        return tree, synced

    def test_sync_only_when_changed(self):
        with patch.dict("os.environ", {"PULSE_FORCE_COMMAND_SYNC": ""}):
            tree, synced = self.make_tree("eins")
            self.assertTrue(asyncio.run(pulse_command_sync.sync_commands(tree))["synced"])
            self.assertFalse(asyncio.run(pulse_command_sync.sync_commands(tree))["synced"])
            self.assertEqual(len(synced), 1)
            changed, synced2 = self.make_tree("zwei")
            self.assertTrue(asyncio.run(pulse_command_sync.sync_commands(changed))["synced"])
            self.assertTrue(asyncio.run(pulse_command_sync.sync_commands(changed, force=True))["synced"])


class ErrorPageTests(unittest.TestCase):
    def test_unexpected_error_shows_id_not_traceback(self):
        from fastapi.testclient import TestClient
        import webserver as ws

        @ws.app.get("/__test_boom")
        async def boom():
            raise RuntimeError("geheimes Detail")

        @ws.app.get("/api/__test_boom")
        async def boom_api():
            raise RuntimeError("geheimes Detail")
        try:
            client = TestClient(ws.app, raise_server_exceptions=False)
            with patch("builtins.print"), patch("traceback.print_exception"):
                page = client.get("/__test_boom")
                api = client.get("/api/__test_boom")
        finally:
            ws.app.router.routes = [r for r in ws.app.router.routes if "__test_boom" not in getattr(r, "path", "")]
        self.assertEqual(page.status_code, 500)
        self.assertIn("Fehler-ID", page.text)
        self.assertNotIn("geheimes Detail", page.text)
        self.assertEqual(api.status_code, 500)
        self.assertIn("error_id", api.json())


class OAuthTests(unittest.TestCase):
    def run_with(self, responses=None, error=None):
        import httpx
        import webserver as ws

        class FakeClient:
            def __init__(self, *a, **k):
                self.responses = list(responses or [])

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def post(self, *a, **k):
                if error:
                    raise error
                return self.responses.pop(0)

            async def get(self, *a, **k):
                return self.responses.pop(0)
        with patch.object(ws.httpx, "AsyncClient", FakeClient):
            return asyncio.run(ws.discord_oauth_identify("code", "https://x/callback"))

    def response(self, status, payload):
        return SimpleNamespace(status_code=status, json=lambda: payload)

    def test_rate_limit_timeout_and_success(self):
        import httpx
        user, problem = self.run_with([self.response(429, {})])
        self.assertIsNone(user)
        self.assertIn("in einer Minute", problem)
        user, problem = self.run_with(error=httpx.ConnectTimeout("t"))
        self.assertIn("rechtzeitig", problem)
        user, problem = self.run_with([self.response(503, {})])
        self.assertIn("nicht erreichbar", problem)
        user, problem = self.run_with([self.response(200, {"access_token": "a"}), self.response(200, {"id": "123", "username": "max"})])
        self.assertEqual(user["id"], "123")
        self.assertEqual(problem, "")


class TicketDedupeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db.stop()
        self.tmp.cleanup()

    def test_indexed_lookup(self):
        pulse_db.create_ticket(10, 1, 42, "Max", "support")
        self.assertEqual(pulse_db.open_ticket_for_user(1, 42)["channel_id"], "10")
        self.assertIsNone(pulse_db.open_ticket_for_user(1, 43))
        self.assertIsNone(pulse_db.open_ticket_for_user(2, 42))

    def test_two_fast_clicks_create_one_ticket(self):
        from cogs import tickets
        created, messages = [], []

        async def slow_create(interaction, key, panel_id, answers):
            await asyncio.sleep(0.02)
            created.append(key)

        def interaction():
            async def send(text, ephemeral=False):
                messages.append(text)
            return SimpleNamespace(guild=SimpleNamespace(id=1, get_channel=lambda _: None),
                                   user=SimpleNamespace(id=42), followup=SimpleNamespace(send=send))

        async def scenario():
            await asyncio.gather(tickets.create_ticket_channel(interaction(), "support", None, []),
                                 tickets.create_ticket_channel(interaction(), "support", None, []))
        with patch.object(tickets, "_create_ticket_channel", slow_create):
            asyncio.run(scenario())
        self.assertEqual(created, ["support"])
        self.assertIn("gerade erstellt", messages[0])
        self.assertEqual(tickets._OPENING_TICKETS, set())


if __name__ == "__main__":
    unittest.main()
