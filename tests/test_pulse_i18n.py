"""Tests for per-user Pulse locale preferences and deterministic fallbacks."""
import unittest
from unittest.mock import patch

import pulse_i18n


class LocaleResolutionTests(unittest.TestCase):
    def test_explicit_preferences_override_discord_locale(self):
        self.assertEqual(pulse_i18n.resolve_locale("de", "en-US"), "de")
        self.assertEqual(pulse_i18n.resolve_locale("en", "de"), "en")

    def test_auto_uses_supported_discord_locale_and_normalizes_regions(self):
        self.assertEqual(pulse_i18n.resolve_locale("auto", "en-US"), "en")
        self.assertEqual(pulse_i18n.resolve_locale("auto", "de-DE"), "de")
        self.assertEqual(pulse_i18n.resolve_locale("auto", "en_US"), "en")

    def test_auto_falls_back_to_german_for_unsupported_or_missing_locale(self):
        self.assertEqual(pulse_i18n.resolve_locale("auto", "fr"), "de")
        self.assertEqual(pulse_i18n.resolve_locale("auto", None), "de")

    def test_invalid_preference_is_rejected(self):
        with self.assertRaises(ValueError):
            pulse_i18n.normalize_preference("fr")
        self.assertEqual(pulse_i18n.normalize_preference("EN-gb"), "en")


class LocaleStorageTests(unittest.TestCase):
    def test_setting_key_is_scoped_to_one_user(self):
        self.assertEqual(pulse_i18n.setting_key(42), "pulse_locale:42")
        self.assertNotEqual(pulse_i18n.setting_key(42), pulse_i18n.setting_key(43))

    def test_missing_preference_uses_discord_locale(self):
        with patch("pulse_db.get_setting", return_value="auto") as read:
            self.assertEqual(pulse_i18n.get_locale(42, "en-GB"), "en")
        read.assert_called_once_with("pulse_locale:42", "auto")

    def test_stored_preference_is_read_and_honored(self):
        with patch("pulse_db.get_setting", return_value="de"):
            self.assertEqual(pulse_i18n.get_locale(42, "en-US"), "de")

    def test_broken_storage_falls_back_without_raising(self):
        with patch("pulse_db.get_setting", side_effect=OSError("database unavailable")):
            self.assertEqual(pulse_i18n.get_locale(42, "en-US"), "en")

    def test_set_locale_persists_only_validated_value(self):
        with patch("pulse_db.set_setting") as save:
            self.assertEqual(pulse_i18n.set_locale(42, "en"), "en")
        save.assert_called_once_with("pulse_locale:42", "en")
        with self.assertRaises(ValueError):
            pulse_i18n.set_locale(42, "fr")
        with self.assertRaises(ValueError):
            pulse_i18n.set_locale(None, "de")


class TranslationTests(unittest.TestCase):
    def test_translations_support_formatting_and_fallback(self):
        self.assertEqual(
            pulse_i18n.translate("en", "profile_title", display_name="Nico"),
            "👤 Your Pulse profile · Nico",
        )
        self.assertEqual(
            pulse_i18n.translate("de", "profile_title", display_name="Nico"),
            "👤 Dein Pulse-Profil · Nico",
        )
        self.assertEqual(pulse_i18n.translate("en", "unknown_key"), "unknown_key")

    def test_help_sections_are_localized_without_changing_command_names(self):
        self.assertEqual(pulse_i18n.translate_help_section("en", "⏱ Dienst & ich"), "⏱ Duty & me")
        self.assertEqual(pulse_i18n.translate_help_section("de", "⏱ Dienst & ich"), "⏱ Dienst & ich")
        self.assertEqual(pulse_i18n.translate_help_section("en", "Custom section"), "Custom section")


if __name__ == "__main__":
    unittest.main()
