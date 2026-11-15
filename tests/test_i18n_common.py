"""Audit #10: häufige Bot-Antworten und Fehlertexte werden zentral für englische Nutzer übersetzt (ohne Discord-Verbindung)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import pulse_command_errors as errors
import pulse_i18n as i18n


class TranslateTests(unittest.TestCase):
    def test_known_sentences_and_error_patterns(self):
        self.assertEqual(i18n.translate_common("en", "❌ Kein Zugriff."), "❌ No access.")
        self.assertEqual(i18n.translate_common("en-US", "Ticket nicht gefunden."), "Ticket not found.")
        self.assertEqual(i18n.translate_common("en", "❌ Dafür fehlen dir Discord-Rechte: **ban members**."), "❌ You are missing Discord permissions: **ban members**.")
        self.assertEqual(i18n.translate_common("en", "⏳ Bitte warte noch **7 s**, bevor du den Befehl erneut nutzt."), "⏳ Please wait **7 s** before using the command again.")
        self.assertIn("Error ID **ABC123**", i18n.translate_common("en", "❌ Dabei ist ein unerwarteter Fehler aufgetreten. Fehler-ID **ABC123** – das Team findet sie im Log."))

    def test_german_unknown_and_non_text_stay_unchanged(self):
        self.assertEqual(i18n.translate_common("de", "❌ Kein Zugriff."), "❌ Kein Zugriff.")
        self.assertEqual(i18n.translate_common("fr", "❌ Kein Zugriff."), "❌ Kein Zugriff.")           # nicht unterstützt -> Deutsch
        self.assertEqual(i18n.translate_common("en", "Ein ganz neuer Satz"), "Ein ganz neuer Satz")
        self.assertIsNone(i18n.translate_common("en", None))
        self.assertEqual(i18n.translate_common(None, "❌ Kein Zugriff."), "❌ Kein Zugriff.")

    def test_every_translation_is_nonempty_keeps_markers_and_differs_from_the_source(self):
        for german, english in i18n.COMMON_EN.items():
            self.assertTrue(english.strip())
            self.assertNotEqual(german, english)
            for marker in ("❌", "✅", "⏳", "📅", "🎫", "🔔", "🔒"):
                self.assertEqual(german.startswith(marker), english.startswith(marker), german)


class WrapperTests(unittest.TestCase):
    def setUp(self):
        self.sent = []

        async def fake_send(response_self, content=None, *args, **kwargs):
            self.sent.append((content, kwargs))
        patcher = patch.object(discord.InteractionResponse, "send_message", fake_send)
        patcher.start()
        self.addCleanup(patcher.stop)
        i18n._installed = False
        self.addCleanup(setattr, i18n, "_installed", False)
        i18n.install_response_translation()

    def response(self, user_id=1, locale="en-US"):
        response = object.__new__(discord.InteractionResponse)
        response._parent = SimpleNamespace(user=SimpleNamespace(id=user_id), locale=locale)
        return response

    def test_english_user_gets_english_and_the_flags_are_kept(self):
        with patch.object(i18n, "get_preference", return_value="auto"):
            asyncio.run(self.response().send_message("❌ Kein Zugriff.", ephemeral=True))
        self.assertEqual(self.sent, [("❌ No access.", {"ephemeral": True})])

    def test_preference_beats_the_discord_locale_and_german_users_are_untouched(self):
        with patch.object(i18n, "get_preference", return_value="de"):
            asyncio.run(self.response(locale="en-US").send_message("❌ Kein Zugriff."))
        with patch.object(i18n, "get_preference", return_value="en"):
            asyncio.run(self.response(locale="de").send_message("❌ Kein Zugriff."))
        with patch.object(i18n, "get_preference", return_value="auto"):
            asyncio.run(self.response(locale="de").send_message("❌ Kein Zugriff."))
        self.assertEqual([c for c, _ in self.sent], ["❌ Kein Zugriff.", "❌ No access.", "❌ Kein Zugriff."])

    def test_content_keyword_unknown_text_and_embeds_pass_through(self):
        with patch.object(i18n, "get_preference", return_value="en"):
            asyncio.run(self.response().send_message(content="Ticket nicht gefunden."))
            asyncio.run(self.response().send_message("Frei formulierter Text"))
            asyncio.run(self.response().send_message(embed="E"))
        self.assertEqual(self.sent[0], ("Ticket not found.", {}))
        self.assertEqual(self.sent[1][0], "Frei formulierter Text")
        self.assertEqual(self.sent[2], (None, {"embed": "E"}))

    def test_a_failing_lookup_still_sends_the_original(self):
        with patch.object(i18n, "get_locale", side_effect=RuntimeError("db")):
            asyncio.run(self.response().send_message("❌ Kein Zugriff."))
        self.assertEqual(self.sent, [("❌ Kein Zugriff.", {})])

    def test_install_is_idempotent(self):
        self.assertFalse(i18n.install_response_translation())


class ErrorHandlerTests(unittest.TestCase):
    def test_error_message_follows_the_users_language(self):
        from discord import app_commands
        interaction = SimpleNamespace(user=SimpleNamespace(id=5), locale="en-US", command=None,
                                      response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()), followup=SimpleNamespace(send=AsyncMock()))
        with patch.object(i18n, "get_preference", return_value="auto"):
            message = asyncio.run(errors.handle_app_command_error(interaction, app_commands.NoPrivateMessage()))
        self.assertEqual(message, "❌ This command only works on the server, not in direct messages.")
        with patch.object(i18n, "get_preference", return_value="de"):
            message = asyncio.run(errors.handle_app_command_error(interaction, app_commands.NoPrivateMessage()))
        self.assertIn("nur auf dem Server", message)


if __name__ == "__main__":
    unittest.main()
