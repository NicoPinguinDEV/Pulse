"""Phase 3: background tasks (P-07), on_ready once (P-08), command sync only on change (P-09)."""
import ast
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

ROOT = Path(__file__).resolve().parents[1]
from pulse_ready import first_ready
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)


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


    def test_critical_task_bypasses_capacity_and_regular_task_is_closed(self):
        async def work():
            await asyncio.sleep(0)
            return "done"

        async def scenario():
            with patch.object(pulse_tasks, "MAX_RUNNING", 0), patch("builtins.print"):
                dropped_coro = work()
                dropped = pulse_tasks.spawn(dropped_coro, name="capacity-drop")
                self.assertIsNone(dropped)
                self.assertIsNone(dropped_coro.cr_frame, "rejected coroutine must be closed")

                critical = pulse_tasks.spawn(work(), name="critical-task", critical=True)
                self.assertIsNotNone(critical)
                self.assertIn(critical, pulse_tasks._RUNNING)
                self.assertEqual(await critical, "done")
                await asyncio.sleep(0)
                self.assertNotIn(critical, pulse_tasks._RUNNING)

        asyncio.run(scenario())


    def test_critical_task_bypasses_capacity_and_regular_task_is_closed(self):
        async def work():
            await asyncio.sleep(0)
            return "done"

        async def scenario():
            with patch.object(pulse_tasks, "MAX_RUNNING", 0), patch("builtins.print"):
                dropped_coro = work()
                dropped = pulse_tasks.spawn(dropped_coro, name="capacity-drop")
                self.assertIsNone(dropped)
                self.assertIsNone(dropped_coro.cr_frame, "rejected coroutine must be closed")

                critical = pulse_tasks.spawn(work(), name="critical-task", critical=True)
                self.assertIsNotNone(critical)
                self.assertIn(critical, pulse_tasks._RUNNING)
                self.assertEqual(await critical, "done")
                await asyncio.sleep(0)
                self.assertNotIn(critical, pulse_tasks._RUNNING)

        asyncio.run(scenario())

    def test_core_background_tasks_use_the_managed_registry(self):
        managed_files = (
            "main.py", "pulse_ultimate.py", "webserver.py",
            "cogs/ai_moderation.py", "cogs/server_stats.py", "cogs/recovery.py",
            "cogs/teamliste.py", "cogs/role_nickname_prefixes.py", "cogs/advanced_logs.py",
        )
        for filename in managed_files:
            with self.subTest(filename=filename):
                tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
                direct = [
                    ast.unparse(node)
                    for node in ast.walk(tree)
                    if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in {"create_task", "ensure_future"}
                    and getattr(node.func.value, "id", "") not in {"db"}
                ]
                self.assertEqual(direct, [], "direkte Task-Erzeugung muss über pulse_tasks.spawn laufen")


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


class JobRegistryTests(unittest.TestCase):
    def test_runs_errors_and_lag_are_recorded(self):
        import pulse_jobs

        @pulse_jobs.tracked("test-job", 60)
        async def ok():
            return 1

        @pulse_jobs.tracked("test-job-fail", 60)
        async def fail():
            raise ValueError("kaputt")
        self.assertEqual(asyncio.run(ok()), 1)
        with patch("traceback.print_exception"), patch("builtins.print"):
            self.assertIsNone(asyncio.run(fail()))  # Phase 14.6: recorded, not re-raised (would end the tasks.loop)
        jobs = {j["name"]: j for j in pulse_jobs.snapshot()["jobs"]}
        self.assertEqual(jobs["test-job"]["runs"], 1)
        self.assertTrue(jobs["test-job"]["last_ok"])
        self.assertEqual(jobs["test-job-fail"]["failures"], 1)
        self.assertIn("ValueError", jobs["test-job-fail"]["last_error"])
        with patch("builtins.print") as printed:
            pulse_jobs.record_loop_lag(60, 60.5)
        self.assertTrue(printed.called)
        self.assertGreaterEqual(pulse_jobs.snapshot()["lag"]["max"], 0.5)

    def test_system_page_lists_jobs_memory_and_sync_button(self):
        from fastapi.testclient import TestClient
        import pulse_jobs
        import webserver as ws
        from tests.test_pulse_admin_area import ADMIN, fake_auth_for
        asyncio.run(pulse_jobs.tracked("Sichtbarer Job", 60)(lambda: asyncio.sleep(0))())
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            html = TestClient(ws.app).get("/system").text
        self.assertIn("Sichtbarer Job", html)
        self.assertIn("Event-Loop", html)
        self.assertIn("Arbeitsspeicher", html)
        self.assertIn("/admin/commands/sync", html)


class ActivityScheduleTests(unittest.TestCase):
    def test_registered_loops_keep_their_schedule(self):
        from datetime import time as dtime
        from cogs.activity_check import ActivityCheckCog, BERLIN_TZ
        self.assertEqual(ActivityCheckCog.activity_warning_check.minutes, 1)
        self.assertEqual(ActivityCheckCog.activity_check_recovery.minutes, 5)
        self.assertEqual(ActivityCheckCog.daily_activity_check.time, [dtime(hour=6, minute=0, tzinfo=BERLIN_TZ)])


class ErrorBufferTests(unittest.TestCase):
    def test_recent_errors_are_bounded_and_shown(self):
        import pulse_logging
        from fastapi.testclient import TestClient
        import webserver as ws
        from tests.test_pulse_admin_area import ADMIN, fake_auth_for
        for i in range(pulse_logging.MAX_RECENT + 5):
            pulse_logging.report_error("test", f"Fehler {i}", "ABC123")
        self.assertEqual(len(pulse_logging._RECENT), pulse_logging.MAX_RECENT)
        self.assertEqual(pulse_logging.recent(1)[0]["message"], f"Fehler {pulse_logging.MAX_RECENT + 4}")
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            html = TestClient(ws.app).get("/system").text
        self.assertIn("Letzte Fehler", html)
        self.assertIn("ABC123", html)

    def test_safe_console_turns_emoji_into_replacement_on_cp1252(self):
        import io
        import sys
        import pulse_logging
        raw = io.BytesIO()
        stream = io.TextIOWrapper(raw, encoding="cp1252")
        with patch.object(sys, "stdout", stream), patch.object(sys, "stderr", stream):
            pulse_logging.safe_console()
            print("❌ Fehler")  # raised UnicodeEncodeError before
        stream.flush()
        self.assertEqual(raw.getvalue().decode("cp1252").strip(), "? Fehler")


class LiveCacheTests(unittest.TestCase):
    def test_live_numbers_computed_once_within_15_seconds(self):
        from fastapi.testclient import TestClient
        import webserver as ws
        from tests.test_pulse_admin_area import TEAMLER, fake_auth_for
        route = next(r for r in ws.app.routes if getattr(r, "path", "") == "/api/pulse/live")
        cache = route.endpoint.__code__.co_freevars
        self.assertIn("LIVE_CACHE", cache)
        cell = route.endpoint.__closure__[cache.index("LIVE_CACHE")].cell_contents
        cell.update(at=0.0, data=None)
        calls = []
        numbers = route.endpoint.__closure__[cache.index("live_numbers")]
        real = numbers.cell_contents
        numbers.cell_contents = lambda c: calls.append(1) or {"ok": True, "active": 3}
        try:
            with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
                client = TestClient(ws.app)
                first = client.get("/api/pulse/live").json()
                client.get("/api/pulse/live")
        finally:
            numbers.cell_contents = real
            cell.update(at=0.0, data=None)
        self.assertEqual(calls, [1])
        self.assertEqual(first["active"], 3)
        self.assertIn("now", first)


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
