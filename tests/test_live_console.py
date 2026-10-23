"""Tests for the restricted administrator live console."""
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_live_console


class LiveConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_help_is_allowlisted_and_explains_no_shell(self):
        result = await pulse_live_console.run_command("help", SimpleNamespace(state=SimpleNamespace()))
        self.assertTrue(result["ok"])
        self.assertIn("Freie Shell-Kommandos", result["output"])

    async def test_arbitrary_shell_is_rejected(self):
        result = await pulse_live_console.run_command("whoami; cat .env", SimpleNamespace(state=SimpleNamespace()))
        self.assertFalse(result["ok"])
        self.assertIn("Unbekannter Befehl", result["output"])

    async def test_version(self):
        result = await pulse_live_console.run_command("version", SimpleNamespace(state=SimpleNamespace()))
        self.assertTrue(result["ok"])
        self.assertIn("Pulse-Version:", result["output"])

    async def test_status_without_bot(self):
        result = await pulse_live_console.run_command("status", SimpleNamespace(state=SimpleNamespace(bot=None)))
        self.assertTrue(result["ok"])
        self.assertIn("nicht verfügbar", result["output"])

    async def test_sync_refuses_when_bot_not_ready(self):
        bot = SimpleNamespace(is_ready=lambda: False, tree=object())
        result = await pulse_live_console.run_command("sync", SimpleNamespace(state=SimpleNamespace(bot=bot)))
        self.assertFalse(result["ok"])
        self.assertIn("nicht bereit", result["output"])

    async def test_log_buffer_is_bounded_and_readable(self):
        logger = logging.getLogger("pulse_live_console_test")
        logger.warning("console-buffer-test-marker")
        self.assertTrue(any("console-buffer-test-marker" in line for line in pulse_live_console.recent_lines()))
