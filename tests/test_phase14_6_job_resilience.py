"""Phase 14.6: recurring jobs survive errors, back off when they keep failing and stay visible in /healthz.

discord.ext.tasks only retries network errors (OSError, aiohttp.ClientError, TimeoutError, ...). Any other
exception – sqlite3.OperationalError "database is locked", discord.Forbidden, a broken JSON value – used to
end a @tasks.loop for the rest of the process lifetime. No real Discord connection or production data is used.
"""
import asyncio
import contextlib
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from discord.ext import tasks

import pulse_jobs


@contextlib.contextmanager
def _Quiet():
    """Silence the side channels of a recorded job error (log, alert queue, traceback, cp1252 console)."""
    with patch("pulse_logging.report_error") as report_error, patch("pulse_alerts.raise_alert") as alert,             patch("traceback.print_exception") as print_exception, patch("builtins.print") as printed:
        yield report_error, alert, print_exception, printed


class TrackedLoopSurvivesErrorsTests(unittest.TestCase):
    def setUp(self):
        pulse_jobs._JOBS.clear()
        self._paused = patch("pulse_flags.automations_paused", return_value=False)
        self._paused.start()

    def tearDown(self):
        self._paused.stop()

    def test_real_tasks_loop_keeps_running_after_a_database_error(self):
        calls = []

        @tasks.loop(seconds=0.01)
        @pulse_jobs.tracked("Phase14.6 Loop", 0.01)
        async def flaky():
            calls.append(1)
            if len(calls) == 1:
                raise sqlite3.OperationalError("database is locked")
            if len(calls) >= 3:
                flaky.stop()

        async def run():
            flaky.start()
            await asyncio.wait_for(flaky.get_task(), 5)

        with _Quiet():
            asyncio.run(run())
        self.assertGreaterEqual(len(calls), 3, "Schleife endete nach dem ersten Fehler")
        self.assertFalse(flaky.failed())
        entry = {j["name"]: j for j in pulse_jobs.snapshot()["jobs"]}["Phase14.6 Loop"]
        self.assertEqual(entry["failures"], 1)
        self.assertTrue(entry["last_ok"])

    def test_cancellation_still_stops_the_loop(self):
        @tasks.loop(seconds=60)
        @pulse_jobs.tracked("Phase14.6 Shutdown", 60)
        async def slow():
            await asyncio.sleep(30)

        async def run():
            slow.start()
            await asyncio.sleep(0.05)
            task = slow.get_task()
            slow.cancel()
            await asyncio.wait_for(asyncio.gather(task, return_exceptions=True), 2)
            return task

        task = asyncio.run(run())
        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())
        self.assertFalse(slow.is_running())
        self.assertEqual({j["name"]: j for j in pulse_jobs.snapshot()["jobs"]}["Phase14.6 Shutdown"]["failures"], 0)

    def test_error_has_a_traceable_error_id(self):
        @pulse_jobs.tracked("Phase14.6 ID", 300)
        async def fail():
            raise RuntimeError("kaputt")

        with _Quiet() as (report_error, *_):
            asyncio.run(fail())
        entry = {j["name"]: j for j in pulse_jobs.snapshot()["jobs"]}["Phase14.6 ID"]
        self.assertRegex(entry["last_error_id"], r"^[0-9A-F]{6}$")
        report_error.assert_called_once()
        self.assertEqual(report_error.call_args.args[2], entry["last_error_id"])
        self.assertIn("Phase14.6 ID", pulse_jobs.health_summary()["jobs_failing"])

    def test_fast_job_backs_off_and_recovers(self):
        calls = []
        now = [1000.0]

        @pulse_jobs.tracked("Phase14.6 Musik", 8)
        async def music():
            calls.append(now[0])
            if len(calls) <= 3:
                raise sqlite3.OperationalError("database is locked")

        with _Quiet(), patch("pulse_jobs.time.monotonic", side_effect=lambda: now[0]):
            for _ in range(80):  # 80 ticks of 8 s = 640 s
                asyncio.run(music())
                now[0] += 8
        # 1st failure: next tick runs; 2nd: wait 16 s; 3rd: 32 s; then success → normal 8 s cadence.
        self.assertEqual(calls[:4], [1000.0, 1008.0, 1024.0, 1056.0])
        self.assertEqual(calls[4] - calls[3], 8.0)
        entry = {j["name"]: j for j in pulse_jobs.snapshot()["jobs"]}["Phase14.6 Musik"]
        self.assertEqual(entry["consecutive_failures"], 0)
        self.assertNotIn("Phase14.6 Musik", pulse_jobs.health_summary()["jobs_failing"])

    def test_backoff_is_capped_at_five_minutes_and_never_skips_slow_jobs(self):
        now = [0.0]
        fast_calls, slow_calls = [], []

        @pulse_jobs.tracked("Phase14.6 Fast", 60)
        async def fast():
            fast_calls.append(now[0])
            raise RuntimeError("dauerhaft kaputt")

        @pulse_jobs.tracked("Phase14.6 Slow", 300)
        async def slow():
            slow_calls.append(now[0])
            raise RuntimeError("dauerhaft kaputt")

        with _Quiet(), patch("pulse_jobs.time.monotonic", side_effect=lambda: now[0]):
            for tick in range(120):  # two hours in 60-second steps
                asyncio.run(fast())
                if tick % 5 == 0:
                    asyncio.run(slow())
                now[0] += 60
        gaps = [b - a for a, b in zip(fast_calls, fast_calls[1:])]
        self.assertLessEqual(max(gaps), 300.0)
        self.assertEqual(gaps[-1], 300.0)
        self.assertEqual(len(slow_calls), 24)  # every 5-minute run still happens


class LoopErrorsAreVisibleTests(unittest.TestCase):
    def setUp(self):
        pulse_jobs._JOBS.clear()
        self._paused = patch("pulse_flags.automations_paused", return_value=False)
        self._paused.start()

    def tearDown(self):
        self._paused.stop()

    def _activity_cog(self, guilds=()):
        from cogs import activity_check
        cog = object.__new__(activity_check.ActivityCheckCog)
        cog.bot = SimpleNamespace(wait_until_ready=AsyncMock(), guilds=list(guilds))
        return activity_check, cog

    def test_daily_activity_check_survives_a_locked_database(self):
        activity_check, cog = self._activity_cog()
        morning = activity_check.datetime(2026, 11, 2, 6, 0, tzinfo=activity_check.BERLIN_TZ)
        with _Quiet(), patch.object(activity_check, "datetime", wraps=activity_check.datetime) as dt, \
                patch.object(activity_check, "get_config", side_effect=sqlite3.OperationalError("database is locked")):
            dt.now.return_value = morning
            asyncio.run(activity_check.ActivityCheckCog.daily_activity_check.coro(cog))  # must not raise
            asyncio.run(activity_check.ActivityCheckCog.activity_check_recovery.coro(cog))
        failing = pulse_jobs.health_summary()["jobs_failing"]
        self.assertIn("Activity-Check täglich (06:00)", failing)
        self.assertIn("Activity-Check Nachholen (5 min)", failing)

    def test_failed_daily_check_message_is_not_reported_as_ok(self):
        activity_check, cog = self._activity_cog()
        morning = activity_check.datetime(2026, 11, 2, 6, 0, tzinfo=activity_check.BERLIN_TZ)
        channel = SimpleNamespace(guild=SimpleNamespace(get_role=lambda _id: None))
        cog.bot.get_channel = lambda _id: channel
        cog.send_check_message = AsyncMock(side_effect=RuntimeError("Discord 403"))
        with _Quiet(), patch.object(activity_check, "datetime", wraps=activity_check.datetime) as dt, \
                patch.object(activity_check.discord, "TextChannel", SimpleNamespace), \
                patch.object(activity_check, "get_config", side_effect=lambda key: "123" if key == "activity_channel_id" else None):
            dt.now.return_value = morning
            asyncio.run(activity_check.ActivityCheckCog.daily_activity_check.coro(cog))
        cog.send_check_message.assert_awaited_once()
        self.assertIn("Activity-Check täglich (06:00)", pulse_jobs.health_summary()["jobs_failing"])

    def test_failed_activity_warning_run_is_visible_and_other_guilds_continue(self):
        activity_check, cog = self._activity_cog(guilds=[SimpleNamespace(id=1), SimpleNamespace(id=2)])
        evening = activity_check.datetime(2026, 11, 2, 20, 1, tzinfo=activity_check.BERLIN_TZ)
        cog.process_daily_warnings = AsyncMock(side_effect=[sqlite3.OperationalError("database is locked"), {"status": "ok"}])
        with _Quiet(), patch.object(activity_check, "datetime", wraps=activity_check.datetime) as dt:
            dt.now.return_value = evening
            asyncio.run(activity_check.ActivityCheckCog.activity_warning_check.coro(cog))
        self.assertEqual(cog.process_daily_warnings.await_count, 2)
        self.assertIn("Activity-Check Warnungen (1 min)", pulse_jobs.health_summary()["jobs_failing"])

    def test_voice_duty_loop_survives_a_locked_settings_database(self):
        from cogs import voice_support
        cog = object.__new__(voice_support.VoiceSupport)
        cog.bot = SimpleNamespace()
        with _Quiet(), patch.object(voice_support.db, "get_setting", side_effect=sqlite3.OperationalError("database is locked")):
            asyncio.run(voice_support.VoiceSupport.duty_status_loop.coro(cog))  # must not raise
            asyncio.run(voice_support.VoiceSupport.music_loop.coro(cog))
        failing = pulse_jobs.health_summary()["jobs_failing"]
        self.assertIn("Voice-Support Bereitschaft (1 min)", failing)
        self.assertIn("Voice-Support Musik (8 s)", failing)

    def test_suite_automation_failure_is_visible_and_later_steps_still_run(self):
        from cogs import pulse_suite
        cog = object.__new__(pulse_suite.PulseSuite)
        cog.guild = lambda: object()
        steps = ["sync_members", "warn_sync", "reminders", "activity_reminder", "long_shift_check",
                 "inactivity", "reports", "shift_reminders", "update_presence"]
        for step in steps:
            setattr(cog, step, AsyncMock())
        cog.sync_members.side_effect = RuntimeError("Discord 403")
        with _Quiet():
            asyncio.run(pulse_suite.PulseSuite.auto.coro(cog))
        for step in steps:
            getattr(cog, step).assert_awaited_once()
        self.assertIn("Suite-Automatik (5 min)", pulse_jobs.health_summary()["jobs_failing"])

    def test_teamos_housekeeping_member_load_failure_is_visible(self):
        from cogs import pulse_next as cog_module
        cog = object.__new__(cog_module.PulseNextEvents)
        cog.bot = SimpleNamespace()
        with _Quiet(), patch.object(cog_module.pulse_next, "run_workflows", AsyncMock()), \
                patch.object(cog_module.pulse_next, "members", side_effect=sqlite3.OperationalError("database is locked")):
            cog._housekeeping_sync(SimpleNamespace(id=1))
        self.assertIn("TeamOS-Housekeeping (5 min)", pulse_jobs.health_summary()["jobs_failing"])


    def test_housekeeping_notification_failure_does_not_skip_later_steps(self):
        import sys
        from cogs import pulse_pro
        cls = next(v for v in vars(pulse_pro).values() if isinstance(v, type) and hasattr(v, "housekeeping_loop"))
        cog = object.__new__(cls)
        cog.bot = SimpleNamespace()
        cog.guild = lambda: object()
        cog.manager_members = lambda _guild: [SimpleNamespace(id=7)]
        fake_web = SimpleNamespace(
            load_config=lambda: {}, cleanup_expired_team_warnings=AsyncMock(return_value={}),
            cleanup_expired_warn_restrictions=AsyncMock(return_value={}), process_expired_probations=AsyncMock(return_value={}),
            process_overdue_shifts=AsyncMock(return_value={}), load_json=lambda *_a: {}, APPS_FILE="apps.json",
            get_loas=lambda: {})
        presence = SimpleNamespace(update=AsyncMock())
        overdue = [{"id": 1, "title": "T", "due_at": "2020-01-01T00:00:00+00:00", "status": "open", "assignee_id": 5}]
        tickets = [{"id": 2, "status": "open", "opened_at": "2020-01-01T00:00:00+00:00", "category": "c", "user_name": "u"}]
        with _Quiet(), patch.dict(sys.modules, {"webserver": fake_web, "pulse_presence": presence}),                 patch("pulse_retention.due_today", return_value=False),                 patch.object(pulse_pro.db, "list_tasks", return_value=overdue),                 patch.object(pulse_pro.db, "list_tickets", return_value=tickets),                 patch.object(pulse_pro.db, "notify", side_effect=sqlite3.OperationalError("database is locked")) as notify:
            asyncio.run(cls.housekeeping_loop.coro(cog))
        self.assertEqual(notify.call_count, 2, "Ticket-Erinnerung wurde nach dem Aufgabenfehler übersprungen")
        presence.update.assert_awaited_once()
        self.assertIn("Housekeeping (5 min)", pulse_jobs.health_summary()["jobs_failing"])

    def test_ai_moderation_worker_survives_a_broken_stored_review_message_id(self):
        from cogs import ai_moderation
        cls = next(v for v in vars(ai_moderation).values() if isinstance(v, type) and hasattr(v, "_worker"))
        cog = object.__new__(cls)
        added = []
        cog.bot = SimpleNamespace(wait_until_ready=AsyncMock(), add_view=lambda view, message_id: added.append(message_id),
                                  is_closed=lambda: True)
        cog.cases = {"a": {"status": "pending", "review_message_id": "kaputt"},
                     "b": {"status": "pending", "review_message_id": "42"}}
        with _Quiet(), patch.object(ai_moderation, "ModerationReviewView", lambda *a: object()):
            asyncio.run(cog._worker())  # must not raise
        self.assertEqual(added, [42])


if __name__ == "__main__":
    unittest.main()
