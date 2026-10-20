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


if __name__ == "__main__":
    unittest.main()
