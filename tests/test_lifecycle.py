"""Phase 9: import without side effects, repeatable start/stop, cleanup after a failed start, no duplicate jobs."""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

# Runs in a fresh interpreter whose working directory is an empty temp folder. Records every file-based SQLite
# connection, every created folder and every .env load during the import – none may happen.
IMPORT_PROBE = r"""
import json, os, pathlib, sqlite3, sys
sys.path.insert(0, sys.argv[1])
events = []
_connect = sqlite3.connect
def connect(path, *a, **k):
    if str(path) != ":memory:":
        events.append("sqlite:" + os.path.basename(str(path)))
    return _connect(path, *a, **k)
sqlite3.connect = connect
_makedirs = os.makedirs
os.makedirs = lambda name, *a, **k: (events.append("mkdir:" + os.path.basename(str(name))), _makedirs(name, *a, **k))[1]
_mkdir = pathlib.Path.mkdir
pathlib.Path.mkdir = lambda self, *a, **k: (events.append("mkdir:" + self.name), _mkdir(self, *a, **k))[1]
import dotenv
dotenv.load_dotenv = lambda *a, **k: events.append("dotenv")
start = os.getcwd()
for name in sys.argv[2].split(","):
    __import__(name)
print("RESULT=" + json.dumps({"events": events, "cwd_changed": os.getcwd() != start,
                              "files": sorted(os.listdir(start))}))
"""


def probe_import(modules: str) -> dict:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        env = {k: v for k, v in os.environ.items() if k != "PULSE_DB_PATH"}
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run([sys.executable, "-c", IMPORT_PROBE, str(ROOT), modules], cwd=tmp, env=env,
                                capture_output=True, text=True, encoding="utf-8", timeout=180)
    if result.returncode != 0:
        raise AssertionError(result.stderr[-3000:])
    line = next(x for x in result.stdout.splitlines() if x.startswith("RESULT="))
    return json.loads(line[len("RESULT="):])


class ImportIsolationTests(unittest.TestCase):
    def test_import_webserver_touches_no_database_and_keeps_cwd(self):
        result = probe_import("webserver")
        self.assertFalse(result["cwd_changed"], "import webserver must not change the working directory")
        self.assertNotIn("dotenv", result["events"], "import webserver must not load the checkout's .env")
        self.assertEqual([e for e in result["events"] if e.startswith("sqlite:")], [])
        self.assertEqual(result["files"], [])

    def test_import_main_has_no_process_side_effects(self):
        result = probe_import("main")
        self.assertFalse(result["cwd_changed"], "import main must not change the working directory")
        self.assertNotIn("dotenv", result["events"], "import main must not load .env into the test process")
        self.assertEqual([e for e in result["events"] if e.startswith(("sqlite:", "mkdir:"))], [])
        self.assertEqual(result["files"], [])


class PulseDbLazySchemaTests(unittest.TestCase):
    def test_first_use_creates_tables_without_explicit_init(self):
        import pulse_db
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                patch.object(pulse_db, "DB_PATH", str(Path(tmp) / "fresh.db")):
            self.assertEqual(pulse_db.notifications("1"), [])
            pulse_db.notify("1", "Titel", "Text")
            self.assertEqual(len(pulse_db.notifications("1")), 1)


class AutomationLifecycleTests(unittest.TestCase):
    def setUp(self):
        import pulse_ultimate
        self.ultimate = pulse_ultimate
        self.started = []

        async def fake_loop(bot):
            self.started.append(bot)
            await asyncio.Event().wait()
        self.patch = patch.object(pulse_ultimate, "automation_loop", fake_loop)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(setattr, pulse_ultimate, "_AUTOMATION_TASK", None)

    def test_start_twice_runs_one_task_and_stop_allows_clean_restart(self):
        async def scenario():
            first = await self.ultimate.start_automation("bot")
            again = await self.ultimate.start_automation("bot")
            await asyncio.sleep(0)
            self.assertIs(first, again)
            self.assertEqual(len(self.started), 1)
            await self.ultimate.stop_automation()
            self.assertTrue(first.done())
            await self.ultimate.stop_automation()  # repeatable
            second = await self.ultimate.start_automation("bot")
            await asyncio.sleep(0)
            self.assertIsNot(first, second)
            self.assertFalse(second.done())
            await self.ultimate.stop_automation()
            self.assertTrue(second.done())
        asyncio.run(scenario())

    def test_stop_ends_stall_watchdog(self):
        import pulse_jobs

        async def scenario():
            stop = pulse_jobs.start_stall_watchdog(asyncio.get_running_loop(), interval=0.05)
            await self.ultimate.stop_automation()
            self.assertTrue(stop.is_set())
        asyncio.run(scenario())


class BotCloseTests(unittest.TestCase):
    def test_close_stops_automation_and_webserver(self):
        import main
        import pulse_ultimate

        async def fake_loop(bot):
            await asyncio.Event().wait()

        async def scenario():
            bot = main.CustomBot()
            with patch.object(pulse_ultimate, "automation_loop", fake_loop):
                automation = await pulse_ultimate.start_automation(bot)
            served = asyncio.Event()

            class FakeServer:
                should_exit = False

                async def serve(self):
                    served.set()
                    while not self.should_exit:
                        await asyncio.sleep(0.01)
            bot._pulse_web_server = FakeServer()
            bot._pulse_web_server_task = asyncio.create_task(bot._pulse_web_server.serve())
            await served.wait()
            await bot.close()
            self.assertTrue(automation.done())
            self.assertTrue(bot._pulse_web_server_task.done())
        try:
            asyncio.run(scenario())
        finally:
            pulse_ultimate._AUTOMATION_TASK = None

    def test_webserver_is_started_once(self):
        import main
        created = []

        class FakeServer:
            should_exit = False

            def __init__(self, config):
                created.append(self)

            async def serve(self):
                while not self.should_exit:
                    await asyncio.sleep(0.01)

        async def scenario():
            bot = main.CustomBot()
            with patch("uvicorn.Server", FakeServer), patch("builtins.print"):
                first = bot._start_web_server(app=object())
                second = bot._start_web_server(app=object())
            self.assertIs(first, second)
            self.assertEqual(len(created), 1)
            await bot.close()
            self.assertTrue(first.done())
        asyncio.run(scenario())


SIGTERM_PROBE = r"""
import asyncio, os, signal, sys
sys.path.insert(0, sys.argv[1])
import main

async def run():
    bot = main.CustomBot()
    bot._install_signal_handlers(asyncio.get_running_loop())
    os.kill(os.getpid(), signal.SIGTERM)
    for _ in range(300):
        if bot.is_closed():
            print("CLOSED")
            return
        await asyncio.sleep(0.01)
    print("STILL-OPEN")

asyncio.run(run())
"""


class SigtermTests(unittest.TestCase):
    """Review Phase 9: docker stop / deploy send SIGTERM (tini forwards it). Without a handler Python died at once –
    no close(), no cog_unload, no lifespan shutdown – and exited 143, which the crash-restart timer treats as a crash."""

    def test_sigterm_handler_closes_the_bot_once(self):
        import main
        registered = {}

        class FakeLoop:
            def add_signal_handler(self, sig, callback):
                registered[sig] = callback

            def create_task(self, coro, name=None):
                return asyncio.get_event_loop().create_task(coro, name=name)

        async def scenario():
            import signal
            bot = main.CustomBot()
            closes = []

            async def close():
                closes.append(1)
            bot.close = close
            loop = FakeLoop()
            with patch("builtins.print"):
                bot._install_signal_handlers(loop)
                self.assertIn(signal.SIGTERM, registered)
                registered[signal.SIGTERM]()
                registered[signal.SIGTERM]()  # a second SIGTERM does not start a second shutdown
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            self.assertEqual(closes, [1])
        asyncio.run(scenario())

    def test_no_signal_support_is_tolerated(self):
        import main

        class WindowsLoop:
            def add_signal_handler(self, sig, callback):
                raise NotImplementedError

        async def scenario():
            bot = main.CustomBot()
            bot._install_signal_handlers(WindowsLoop())  # must not raise
            await bot.close()
        asyncio.run(scenario())

    @unittest.skipIf(os.name == "nt", "SIGTERM cannot be delivered to a Python loop on Windows (CI runs Linux)")
    def test_real_sigterm_closes_the_bot(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            result = subprocess.run([sys.executable, "-c", SIGTERM_PROBE, str(ROOT)], cwd=tmp, capture_output=True,
                                    text=True, encoding="utf-8", timeout=120,
                                    env=dict(os.environ, PYTHONIOENCODING="utf-8"))
        self.assertEqual(result.returncode, 0, result.stderr[-2000:])
        self.assertIn("CLOSED", result.stdout)


class LifespanTests(unittest.TestCase):
    def setUp(self):
        import webserver
        self.ws = webserver
        for name, value in (("init_runtime", lambda: None),):
            p = patch.object(webserver, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(setattr, webserver.app.state, "backup_task", None)
        self.addCleanup(setattr, webserver.app.state, "roblox_http_client", None)

    def test_repeated_start_stop_leaves_nothing_running(self):
        loops = []

        async def fake_backup_loop():
            loops.append(1)
            await asyncio.Event().wait()

        async def scenario():
            for _ in range(2):
                async with self.ws._lifespan(self.ws.app):
                    await asyncio.sleep(0)
                    task = self.ws.app.state.backup_task
                    self.assertFalse(task.done())
                self.assertTrue(task.done())
                self.assertTrue(self.ws.app.state.roblox_http_client.is_closed)
        with patch.object(self.ws, "auto_backup_loop", fake_backup_loop):
            asyncio.run(scenario())
        self.assertEqual(len(loops), 2)

    def test_failed_start_cleans_up_and_restart_works(self):
        def broken_backup_loop():
            raise RuntimeError("Start fehlgeschlagen")

        async def fake_backup_loop():
            await asyncio.Event().wait()

        async def scenario():
            with patch.object(self.ws, "auto_backup_loop", broken_backup_loop):
                with self.assertRaises(RuntimeError):
                    async with self.ws._lifespan(self.ws.app):
                        pass
            client = self.ws.app.state.roblox_http_client
            self.assertTrue(client.is_closed, "HTTP pool of a failed start must be closed")
            with patch.object(self.ws, "auto_backup_loop", fake_backup_loop):
                async with self.ws._lifespan(self.ws.app):
                    task = self.ws.app.state.backup_task
                    self.assertFalse(task.done())
                self.assertTrue(task.done())
        asyncio.run(scenario())


class CogUnloadTests(unittest.TestCase):
    def test_suite_unload_stops_its_loop(self):
        from cogs.pulse_suite import PulseSuite

        async def scenario():
            ready = asyncio.Event()
            bot = SimpleNamespace(wait_until_ready=ready.wait, remove_dynamic_items=lambda *a: None)
            cog = PulseSuite.__new__(PulseSuite)
            cog.bot = bot
            cog.auto.start()
            await asyncio.sleep(0)
            self.assertTrue(cog.auto.is_running())
            await cog.cog_unload()
            await asyncio.sleep(0)
            self.assertFalse(cog.auto.is_running())
        asyncio.run(scenario())

    def test_role_prefix_unload_stops_bulk_sync(self):
        from cogs.role_nickname_prefixes import RoleNicknamePrefixes

        async def scenario():
            cog = RoleNicknamePrefixes(SimpleNamespace())
            flushed = []

            async def sync_existing_members():
                await asyncio.Event().wait()

            async def flush():
                flushed.append(1)
            cog.sync_existing_members = sync_existing_members
            cog._flush_state_cache = flush
            self.assertTrue(cog.start_sync_existing_members())
            await asyncio.sleep(0)
            await cog.cog_unload()
            self.assertTrue(cog._bulk_sync_task.done())
        asyncio.run(scenario())

    def test_recovery_unload_stops_background_sync(self):
        import cogs.recovery as recovery

        async def scenario():
            cog = recovery.RecoveryCog(SimpleNamespace())
            stopped_between_users = []

            async def cooperative():  # ends at the next user boundary, like _run_all_sync
                while not cog._stop_requested:
                    await asyncio.sleep(0.01)
                stopped_between_users.append(1)

            async def stuck():  # e.g. a hanging token exchange
                await asyncio.Event().wait()
            cog._sync_task = asyncio.create_task(cooperative())
            await cog.cog_unload()
            self.assertEqual(stopped_between_users, [1])  # graceful, not cancelled
            self.assertFalse(cog._sync_task.cancelled())
            cog._sync_task = asyncio.create_task(stuck())
            with patch.object(recovery, "UNLOAD_GRACE_SECONDS", 0.05):
                await cog.cog_unload()
            self.assertTrue(cog._sync_task.cancelled())
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
