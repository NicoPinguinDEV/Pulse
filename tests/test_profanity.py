"""Schimpfwortfilter: Erkennung, Normalisierung, Fehlalarme, Eskalationsstufen und persistenter Zähler."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import pulse_moderation as pm
import pulse_profanity as pp
import pulse_profanity_strikes as strikes


class DetectionTests(unittest.TestCase):
    def assertFlagged(self, text, config=None):
        self.assertIsNotNone(pp.detect_category(text, config), f"nicht erkannt: {text!r}")

    def assertClean(self, text, config=None):
        self.assertIsNone(pp.detect_category(text, config), f"Fehlalarm: {text!r}")

    def test_normal_words_are_not_flagged(self):
        for text in ("Hallo zusammen, wie geht es euch?", "Der Dienst beginnt um 18 Uhr.", "", "   ", "123 456", "Wir treffen uns im Büro"):
            self.assertClean(text)

    def test_clear_german_words_are_detected(self):
        for text in ("Du Arschloch", "so eine Scheiße", "Scheisse!", "Hurensohn", "du Wichser", "Vollidiot", "Fotze", "Drecksau", "Spasti"):
            self.assertFlagged(text)

    def test_clear_english_words_are_detected(self):
        for text in ("fuck you", "what a bitch", "shit happens", "you asshole", "motherfucker", "bullshit", "son of a bitch", "piece of shit"):
            self.assertFlagged(text)

    def test_case_is_ignored(self):
        for text in ("FICK", "ArScHlOcH", "SCHEISSE", "FUCK"):
            self.assertFlagged(text)

    def test_special_character_evasions(self):
        for text in ("f.i.c.k", "f*ck", "f_ck", "F!CK", "h*rensohn", "H.U.R.E.N.S.O.H.N", "f**k", "sh*t", "p*ssy", "f_i_c_k"):
            self.assertFlagged(text)

    def test_number_evasions(self):
        for text in ("F1CK", "Hur3nsohn", "Ar5chloch", "Sche1ße", "sh1t", "b1tch", "4rschloch"):
            self.assertFlagged(text)

    def test_spaces_between_letters(self):
        for text in ("f u c k", "H U R E N S O H N", "a r s c h l o c h", "du bist ein a r s c h", "fu ck", "s h i t"):
            self.assertFlagged(text)

    def test_repeated_letters_zero_width_and_unicode_tricks(self):
        for text in ("Fiiiiick", "Schweiiine Arschlooooch", "f​u​c​k", "ｆｕｃｋ", "fuсk", "ƒuck", "Hurensöhne", "Möse", "moese"):
            self.assertFlagged(text)

    def test_markdown_and_punctuation_do_not_hide_words(self):
        for text in ("**fick**", "||fuck||", "_scheiße_", "Fick!!!", "„Arschloch“", "(shit)"):
            self.assertFlagged(text)

    def test_phrases_and_compounds(self):
        for text in ("fick dich", "Fickt euch", "Riesenarschloch", "Scheißkerl", "Dummwichser"):
            self.assertFlagged(text)

    def test_harmless_words_do_not_trigger(self):
        for text in (
            "class", "Spaß", "Klassenzimmer", "Assistent", "Wirtschaft", "Shiitake", "Massage", "Bass", "Passwort",
            "Hurra", "Marsch", "Barsch", "Hose", "Titel", "Titanic", "Bastelei", "Schwanzflosse", "Analyse", "Cocktail",
            "Mose und die Propheten", "Moby Dick", "Das ist dick", "fickle weather", "Therapie", "Handschuh", "assume",
            "Ich habe 5 7 3 Äpfel", "a b c d e", "Treffpunkt um 5 Uhr", "Das war ein Missverständnis",
        ):
            self.assertClean(text)

    def test_links_and_custom_emoji_are_not_scanned(self):
        self.assertClean("https://example.com/fuck-this-page")
        self.assertClean("<:scheisse:123456789012345678>")

    def test_categories_can_be_switched_off(self):
        only_english = {"categories": {"de": False, "en": True, "discrimination": False, "gaming": False}}
        self.assertClean("du Arschloch", only_english)
        self.assertFlagged("you asshole", only_english)
        self.assertEqual(pp.detect_category("du Arschloch"), "de")
        self.assertEqual(pp.detect_category("nigger"), "discrimination")
        self.assertEqual(pp.detect_category("kys"), "gaming")

    def test_custom_words_extend_and_removed_words_disable_the_defaults(self):
        self.assertClean("Das ist ein Schnuffelwort")
        self.assertEqual(pp.detect_category("Das ist ein Schnuffelwort", {"custom_terms": ["schnuffelwort"]}), "custom")
        self.assertEqual(pp.detect_category("du bist ein Schnuffel Wort", {"custom_terms": ["schnuffel wort"]}), "custom")
        self.assertClean("Idiot", {"removed_terms": ["idiot"]})
        self.assertFlagged("Arschloch", {"removed_terms": ["idiot"]})

    def test_config_is_bounded_and_defaults_are_all_on(self):
        cfg = pp.normalize_profanity({"custom_terms": ["x"] * 500 + ["y" * 500], "categories": {"de": False}, "junk": 1})
        self.assertEqual(set(cfg), {"categories", "custom_terms", "removed_terms"})
        self.assertFalse(cfg["categories"]["de"])
        self.assertTrue(cfg["categories"]["en"])
        self.assertLessEqual(len(cfg["custom_terms"]), pp.MAX_CUSTOM_TERMS)
        self.assertEqual(pp.normalize_profanity(None)["categories"], {k: True for k in pp.CATEGORIES})

    def test_default_list_is_substantial_and_ships_all_categories(self):
        counts = pp.default_word_counts()
        self.assertEqual(set(counts), set(pp.CATEGORIES))
        self.assertGreaterEqual(sum(counts.values()), 150)
        # Die Beispiele aus der Aufgabenstellung sind enthalten (Dick/Idiot-Sonderfälle siehe Dokumentation).
        for word in ("arschloch", "scheisse", "wichser", "hurensohn", "vollidiot", "schlampe", "titten", "fuck", "asshole", "cunt", "whore"):
            self.assertFlagged(word)


class PolicyTests(unittest.TestCase):
    def test_escalation_steps(self):
        self.assertEqual(pp.profanity_step(1), {"action": "warn", "level": 1, "minutes": 0})
        self.assertEqual(pp.profanity_step(2)["action"], "warn")
        for number in (3, 4, 5, 12):
            self.assertEqual(pp.profanity_step(number)["minutes"], 60)  # exakt 60, keine Verdoppelung
            self.assertEqual(pp.profanity_step(number)["action"], "timeout")

    def test_dm_texts_follow_the_specification(self):
        first = pp.dm_text(1, "BOCHUM RP")
        self.assertEqual(first[0], "⚠️ Pulse AutoMod – Erste Verwarnung")
        self.assertIn("Dies ist deine erste Verwarnung.", first[1])
        second = pp.dm_text(2, "BOCHUM RP")
        self.assertEqual(second[0], "⚠️ Pulse AutoMod – Zweite Verwarnung")
        self.assertIn("60 Minuten", second[1])
        third = pp.dm_text(3, "BOCHUM RP")
        self.assertEqual(third[0], "🔇 Pulse AutoMod – 60 Minuten Timeout")
        self.assertEqual(pp.dm_text(9, "BOCHUM RP")[0], third[0])
        self.assertNotIn("@", pp.dm_text(1, "@everyone **x**")[1])  # keine Erwähnungen/Markdown aus dem Servernamen

    def test_profanity_is_a_separate_opt_in_rule(self):
        self.assertIn("profanity", pm.AUTOMOD_RULES)
        self.assertNotIn("profanity", pm.DEFAULT_AUTOMOD_RULES)
        self.assertIn("profanity", pm.AUTOMOD_RULE_ORDER)
        self.assertIn("profanity", pm.AUTOMOD_RULE_LABELS)
        self.assertEqual(pm.normalize_automod({})["rules"], sorted(pm.DEFAULT_AUTOMOD_RULES))

    def test_match_rules_reports_profanity_without_the_word_and_leaves_other_rules_alone(self):
        hits = pm.match_rules("du Arschloch", rules=["profanity"])
        self.assertEqual([h[0] for h in hits], ["profanity"])
        self.assertNotIn("arsch", hits[0][1].casefold())
        self.assertEqual(pm.match_rules("du Arschloch", rules=["spam", "mentions", "invites", "keywords", "caps"]), [])
        self.assertEqual([h[0] for h in pm.match_rules("verboten", rules=["keywords"], blocked_terms=["verboten"])], ["keywords"])
        self.assertEqual(pm.match_rules("Hallo Welt", rules=["profanity"]), [])


class StrikeStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.patch.stop()
        try:
            self.tmp.cleanup()
        except OSError:
            pass

    def test_counts_per_guild_and_user_and_persists(self):
        a = strikes.register_violation(1, 10, "Anna", 100, 5, "de", 60, now=1000)
        b = strikes.register_violation(1, 10, "Anna", 101, 5, "de", 60, now=1100)
        other_guild = strikes.register_violation(2, 10, "Anna", 102, 5, "de", 60, now=1200)
        other_user = strikes.register_violation(1, 11, "Ben", 103, 5, "en", 60, now=1200)
        self.assertEqual((a["strike_number"], b["strike_number"], other_guild["strike_number"], other_user["strike_number"]), (1, 2, 1, 1))
        self.assertEqual(strikes.count_in_window(1, 10, 60, now=1300), 2)  # neue Verbindung pro Aufruf = "Neustart"

    def test_same_message_is_counted_once(self):
        first = strikes.register_violation(1, 10, "Anna", 555, 5, "de", 60, now=1000)
        self.assertIsNone(strikes.register_violation(1, 10, "Anna", 555, 5, "de", 60, now=1001))
        self.assertEqual(first["strike_number"], 1)
        self.assertEqual(len(strikes.history(1, 10)), 1)

    def test_window_limits_the_count_but_history_is_kept(self):
        strikes.register_violation(1, 10, "Anna", 1, 5, "de", 60, now=0)
        strikes.register_violation(1, 10, "Anna", 2, 5, "de", 60, now=600)
        later = strikes.register_violation(1, 10, "Anna", 3, 5, "de", 60, now=60 * 60 + 601)
        self.assertEqual(later["strike_number"], 1)  # beide alten Verstöße liegen außerhalb des Fensters
        self.assertEqual(len(strikes.history(1, 10)), 3)
        edge = strikes.register_violation(1, 10, "Anna", 4, 5, "de", 60, now=60 * 60 + 700)
        self.assertEqual(edge["strike_number"], 2)

    def test_finalize_and_summary_store_no_message_text(self):
        row = strikes.register_violation(1, 10, "Anna", 7, 5, "de", 60, now=5000)
        strikes.finalize(row["id"], action="3. Verwarnung + Timeout 60 Min.", timeout_minutes=60, outcome="ok", event_id="evt-1")
        stored = strikes.history(1, 10)[0]
        self.assertEqual((stored["timeout_minutes"], stored["outcome"], stored["event_id"], stored["rule"]), (60, "ok", "evt-1", "profanity"))
        self.assertEqual(set(stored), {"id", "guild_id", "user_id", "user_name", "message_id", "channel_id", "rule", "category",
                                       "strike_number", "window_minutes", "action", "timeout_minutes", "outcome", "event_id",
                                       "created_ts", "created_at"})
        self.assertEqual(strikes.summary(1, now=5100), {"total": 1, "users": 1, "timeouts": 1})
        self.assertEqual(strikes.summary(2, now=5100), {"total": 0, "users": 0, "timeouts": 0})


if __name__ == "__main__":
    unittest.main()
