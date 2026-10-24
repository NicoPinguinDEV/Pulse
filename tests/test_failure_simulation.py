"""Phase B (complete mission): failure simulation with fakes – locked SQLite, Discord 403/404/429, timeouts,
broken JSON, missing channels/members. Only temp data, no Discord, no network.
"""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import pulse_db
import pulse_ultimate as pu
import webserver as ws
from tests import test_automation_center as _automation_tests


class _AutomationBase(unittest.TestCase):
    """Setup and helpers of AutomationRouteTests without inheriting (and re-running) its tests."""
    setUp = _automation_tests.AutomationRouteTests.setUp
    tearDown = _automation_tests.AutomationRouteTests.tearDown
    rows = _automation_tests.AutomationRouteTests.rows
    runs = _automation_tests.AutomationRouteTests.runs
    form = _automation_tests.AutomationRouteTests.form


def http_error(cls, status, text="x"):
    return cls(SimpleNamespace(status=status, reason=text), text)


class AutomationCycleFailureTests(_AutomationBase):
    """M-B1: one automation that raised in the 60-second cycle (e.g. "database is locked" while storing its run)
    aborted the whole cycle: every later due automation was skipped and the failing one kept its old next_run_at,
    so it was retried every minute instead of after its interval."""

    def due(self, *names):
        for name in names:
            self.client.post("/ultimate/automations/save", data=self.form(name=name))
        with pu.cx() as c:
            c.execute("UPDATE ultimate_automations SET next_run_at='2000-01-01T00:00:00+00:00'")
        return {r["name"]: r["id"] for r in self.rows()}

    def cycle(self):
        with patch.object(ws, "cleanup_expired_warn_restrictions", AsyncMock(), create=True), \
                patch.object(ws, "send_interview_reminders", AsyncMock(), create=True):
            asyncio.run(pu._automation_cycle(None))

    def test_locked_database_for_one_automation_does_not_stop_the_others(self):
        ids = self.due("Erste", "Zweite")
        real_store = pu._store_run
        first = sorted(ids.values())[0]

        def store(automation, *args, **kwargs):
            if automation["id"] == first:
                raise sqlite3.OperationalError("database is locked")
            return real_store(automation, *args, **kwargs)

        with patch.object(pu, "_store_run", store):
            self.cycle()  # must not raise
        stored = {r["automation_id"] for r in self.runs()}
        self.assertEqual(stored, set(ids.values()) - {first})
        rows = {r["id"]: r for r in self.rows()}
        for automation_id in ids.values():  # both move on to their next interval – no retry every minute
            self.assertGreater(rows[automation_id]["next_run_at"], "2001")
        self.assertNotIn(first, pu._RUNNING)


class DiscordFailureTests(_AutomationBase):
    """Automation steps report Discord 403/404/429 and timeouts as failed steps; nothing is retried in a loop."""

    def run_alert_with(self, exc):
        self.client.post("/ultimate/automations/save", data=self.form(step1_type="security_alert", step1_body="Alarm"))
        [row] = self.rows()
        self.channel.send.side_effect = exc
        result = asyncio.run(pu.execute_automation(self.bot, row, trigger_info={"trigger": "interval"}))
        return result

    def test_403_404_429_and_timeout_are_failed_steps_sent_once(self):
        for exc in (http_error(discord.Forbidden, 403), http_error(discord.NotFound, 404),
                    http_error(discord.HTTPException, 429), asyncio.TimeoutError()):
            with self.subTest(exc=type(exc).__name__):
                with pu.cx() as c:
                    c.execute("DELETE FROM ultimate_automations")
                self.channel.send.reset_mock()
                result = self.run_alert_with(exc)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["steps"][0]["status"], "failed")
                self.assertEqual(self.channel.send.await_count, 1)

    def test_missing_alert_channel_is_reported(self):
        self.guild.get_channel = lambda cid: None
        result = self.run_alert_with(None)
        self.assertEqual(result["steps"][0]["status"], "failed")
        self.assertIn("Alarmkanal", result["steps"][0]["detail"])


class BrokenJsonTests(unittest.TestCase):
    """Broken or half-written JSON runtime files never crash a reader and are never silently overwritten empty."""

    def test_truncated_and_wrong_type_json_fall_back_to_the_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            for content in ('{"team": [1, 2', "", "\x00\x00", "[1, 2, 3]"):
                path = Path(tmp) / "config.json"
                path.write_text(content, encoding="utf-8")
                with self.subTest(content=content[:8]), patch("builtins.print"):
                    value = ws.load_json(str(path), {"fallback": True})
                    self.assertIsInstance(value, (dict, list))
                    self.assertEqual(path.read_text(encoding="utf-8"), content, "Lesen darf die Datei nicht verändern")

    def test_save_json_is_atomic_when_writing_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "team_data.json"
            ws.save_json(str(path), {"stand": 1})
            with patch("json.dump", side_effect=OSError("Datenträger voll")), self.assertRaises(OSError):
                ws.save_json(str(path), {"stand": 2})
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"stand": 1})
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["team_data.json"], "keine halbe .tmp-Datei zurücklassen")

    def test_corrupt_file_is_never_overwritten_by_a_later_save(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "shifts.json"
            path.write_text('{"kaputt": ', encoding="utf-8")
            with patch("builtins.print"):
                self.assertEqual(ws.load_json(str(path), {}), {})
                with self.assertRaises(ws.JsonWriteBlocked):
                    ws.save_json(str(path), {})
            self.assertEqual(path.read_text(encoding="utf-8"), '{"kaputt": ')
            ws._CORRUPT_JSON.pop(str(path.resolve()), None)
            ws._CORRUPT_JSON.pop(__import__("os").path.abspath(path), None)


class LockedDatabaseTests(unittest.TestCase):
    """A database locked by another writer: pulse_db waits (busy timeout) and then reports, it never corrupts."""

    def test_locked_database_raises_and_data_stays_consistent(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = str(Path(tmp) / "pulse.db")
            with patch.object(pulse_db, "DB_PATH", path):
                pulse_db.init_db()
                pulse_db.set_setting("probe", {"v": 1})
                blocker = sqlite3.connect(path, timeout=0.1)
                blocker.execute("BEGIN EXCLUSIVE")
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        pulse_db.set_setting("probe", {"v": 2})
                finally:
                    blocker.rollback()
                    blocker.close()
                self.assertEqual(pulse_db.get_setting("probe", None), {"v": 1})
                pulse_db.set_setting("probe", {"v": 3})  # usable again once the lock is gone
                self.assertEqual(pulse_db.get_setting("probe", None), {"v": 3})


if __name__ == "__main__":
    unittest.main()
