"""AutoMod profanity filter: practically unlimited own word list (chip manager), compatibility and performance.

Only local fakes - temp config and database, no Discord connection.
"""
import tempfile
import time
import tracemalloc
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_moderation as pm
import pulse_profanity as pp
import webserver as ws
from tests.test_guard_admin import GuardAdminRouteTests, auth_with


def words(n, prefix="zork"):
    """Unique, harmless, letters-only test words ("zorkaaa", "zorkaab", ...)."""
    out = []
    for i in range(n):
        suffix, x = "", i
        for _ in range(4):
            suffix = chr(97 + x % 26) + suffix
            x //= 26
        out.append(f"{prefix}{suffix}")
    return out


class WordListUnitTests(unittest.TestCase):
    def test_limit_is_no_longer_200_and_nothing_is_silently_cut_below_the_safety_limit(self):
        terms = words(5000)
        cfg = pp.normalize_profanity({"custom_terms": terms})
        self.assertEqual(cfg["custom_terms"], terms)
        self.assertGreaterEqual(pp.MAX_CUSTOM_TERMS, 10_000)

    def test_duplicates_are_removed_case_insensitively_keeping_the_first(self):
        cfg = pp.normalize_profanity({"custom_terms": ["Foo Bar", "foo bar", "FOO BAR", " foo bar ", "baz"]})
        self.assertEqual(cfg["custom_terms"], ["foo bar", "baz"])

    def test_multiword_entries_stay_together_and_are_detected(self):
        cfg = {"custom_terms": ["dummer zork wicht"], "categories": {k: False for k in pp.CATEGORIES}}
        self.assertEqual(pp.normalize_profanity(cfg)["custom_terms"], ["dummer zork wicht"])
        self.assertEqual(pp.detect_category("Du bist ein dummer zork wicht!", cfg), "custom")
        self.assertIsNone(pp.detect_category("Du bist dummer als ein zork", cfg))

    def test_custom_words_in_a_big_list_are_detected_including_evasions(self):
        terms = words(20_000) + ["schnuffelwort"]
        cfg = {"custom_terms": terms, "categories": {k: False for k in pp.CATEGORIES}}
        for text in ("das ist zorkaaab", "S c h n u f f e l w o r t", "SCHNUFFELWORT", "schnuffe1wort"):
            self.assertEqual(pp.detect_category(text, cfg), "custom", text)
        self.assertEqual(pp.detect_category(f"hallo {terms[19_999]} ok", cfg), "custom", "letztes Wort der Liste")
        self.assertIsNone(pp.detect_category("ein völlig normaler Satz über das Wetter", cfg))

    def test_wildcard_stems_still_work_in_a_big_list(self):
        cfg = {"custom_terms": words(3000) + ["pimpf*", "*quatsch"], "categories": {k: False for k in pp.CATEGORIES}}
        self.assertEqual(pp.detect_category("Pimpfhaft", cfg), "custom")
        self.assertEqual(pp.detect_category("Riesenquatsch", cfg), "custom")
        self.assertIsNone(pp.detect_category("Quatschen", cfg), "*quatsch ist nur ein Wortende")
        self.assertEqual(pp.detect_category("p*mpf", {"custom_terms": ["pimpf"], "categories": {k: False for k in pp.CATEGORIES}}), "custom")

    def test_default_lists_are_unchanged_and_independent_from_custom_words(self):
        before = {k: tuple(v) for k, v in pp.DEFAULT_WORDS.items()}
        counts = pp.default_word_counts()
        cfg = {"custom_terms": words(1000), "removed_terms": ["idiot"]}
        pp.detect_category("irgendwas", cfg)
        self.assertEqual({k: tuple(v) for k, v in pp.DEFAULT_WORDS.items()}, before)
        self.assertEqual(pp.default_word_counts(), counts)
        self.assertEqual(pp.detect_category("Arschloch", cfg), "de")
        self.assertIsNone(pp.detect_category("Idiot", cfg))
        self.assertEqual(pp.detect_category("fick dich", {"custom_terms": words(10)}), "de")

    def test_old_configuration_with_200_entries_is_read_completely(self):
        old = {"categories": {"de": True}, "custom_terms": words(200), "removed_terms": ["x1"]}
        cfg = pp.normalize_profanity(old)
        self.assertEqual(cfg["custom_terms"], old["custom_terms"])
        self.assertEqual(cfg["removed_terms"], ["x1"])

    def test_safety_limits_are_enforced_by_normalisation_only_as_a_hard_cap(self):
        with patch.object(pp, "MAX_CUSTOM_TERMS", 50):
            pp._clean_terms.cache_clear()
            self.assertEqual(len(pp.normalize_profanity({"custom_terms": words(80)})["custom_terms"]), 50)
        pp._clean_terms.cache_clear()
        long = "a" * (pp.MAX_TERM_LENGTH + 40)
        self.assertEqual(len(pp.normalize_profanity({"custom_terms": [long]})["custom_terms"][0]), pp.MAX_TERM_LENGTH)

    def test_matcher_cache_is_bounded(self):
        self.assertLessEqual(pp._build.cache_info().maxsize, 4)
        self.assertLessEqual(pp._clean_terms.cache_info().maxsize, 4)

    def test_big_list_is_fast_and_memory_bounded(self):
        terms = words(50_000)
        cfg = {"custom_terms": terms}
        tracemalloc.start()
        started = time.perf_counter()
        pp.detect_category("hallo welt", cfg)  # builds the matcher once
        build = time.perf_counter() - started
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        self.assertLess(build, 15, f"Matcher-Aufbau für 50.000 Wörter dauerte {build:.1f}s")
        self.assertLess(peak / 1_048_576, 150, f"Spitzen-RAM beim Aufbau {peak / 1_048_576:.0f} MiB")
        messages = ["Das ist ein ganz normaler Satz mit zwanzig Wörtern, den ihr alle kennt und mag, ok?"] * 200
        started = time.perf_counter()
        for text in messages:
            self.assertIsNone(pp.detect_category(text, {**cfg, "categories": {k: False for k in pp.CATEGORIES}}))
        per_message = (time.perf_counter() - started) / len(messages)
        self.assertLess(per_message, 0.05, f"{per_message * 1000:.1f} ms pro Nachricht")

    def test_hundreds_of_wildcard_tokens_in_a_big_list_stay_fast(self):
        cfg = {"custom_terms": words(30_000), "categories": {k: False for k in pp.CATEGORIES}}
        pp.detect_category("x", cfg)
        text = " ".join(["z*rkaaab", "zo_kaaab", "z**kaaab"] * 130)
        started = time.perf_counter()
        pp.detect_category(text, cfg)
        self.assertLess(time.perf_counter() - started, 1.0)


class WordListRouteTests(unittest.TestCase):
    setUp = GuardAdminRouteTests.setUp
    tearDown = GuardAdminRouteTests.tearDown
    saved = GuardAdminRouteTests.saved

    def post(self, custom, **extra):
        form = {"profanity_enabled": "1", "profanity_cat_de": "1", "custom_terms": custom, **extra}
        return self.client.post("/admin/security/profanity", data=form, follow_redirects=False)

    def test_more_than_200_words_are_saved_and_come_back_after_reload(self):
        terms = words(1500)
        response = self.post("\n".join(terms))
        self.assertIn("t=ok", response.headers["location"])
        self.assertEqual(self.saved()["pulse_automod"]["profanity"]["custom_terms"], terms)
        page = self.client.get("/admin/security").text
        self.assertIn(">1500</span> gespeichert", page)
        self.assertIn(terms[-1], page)

    def test_very_large_list_is_saved_completely(self):
        terms = words(30_000)
        response = self.post("\n".join(terms))
        self.assertIn("t=ok", response.headers["location"])
        stored = self.saved()["pulse_automod"]["profanity"]["custom_terms"]
        self.assertEqual(len(stored), 30_000)
        self.assertEqual(stored[0], terms[0])
        self.assertEqual(stored[-1], terms[-1])

    def test_pasted_lines_are_imported_deduplicated_and_multiword_terms_survive(self):
        self.post("Zork Eins\r\n\r\nzork eins\nZORK EINS\n  dummer zork wicht  \nzork zwei")
        stored = self.saved()["pulse_automod"]["profanity"]["custom_terms"]
        self.assertEqual(stored, ["zork eins", "dummer zork wicht", "zork zwei"])

    def test_exceeding_the_safety_limit_is_reported_and_changes_nothing(self):
        self.post("alt eins\nalt zwei")
        before = self.saved()
        with patch.object(pp, "MAX_CUSTOM_TERMS", 3):
            response = self.post("\n".join(words(4)))
        self.assertIn("t=err", response.headers["location"])
        self.assertEqual(self.saved(), before, "nichts darf teilweise oder gekürzt gespeichert werden")

    def test_too_long_term_and_oversized_input_are_rejected_not_cut(self):
        before_exists = self.config_path.exists()
        r = self.post("x" * (pp.MAX_TERM_LENGTH + 1))
        self.assertIn("t=err", r.headers["location"])
        with patch.object(pp, "MAX_INPUT_CHARS", 100):
            r = self.post("\n".join(words(50)))
        self.assertIn("t=err", r.headers["location"])
        self.assertEqual(self.config_path.exists(), before_exists)

    def test_removed_default_words_stay_separate_and_other_settings_are_kept(self):
        self.post("zork eins", removed_terms="idiot")
        profanity = self.saved()["pulse_automod"]["profanity"]
        self.assertEqual(profanity["custom_terms"], ["zork eins"])
        self.assertEqual(profanity["removed_terms"], ["idiot"])
        self.assertEqual(pm.normalize_automod(self.saved()["pulse_automod"])["profanity"], profanity | {"categories": profanity["categories"]})

    def test_saving_does_not_enable_other_protections(self):
        self.post("zork eins")
        automod = self.saved()["pulse_automod"]
        self.assertFalse(automod["enabled"], "AutoMod bleibt global aus")
        self.assertIn("profanity", automod["rules"])
        self.assertFalse(self.saved().get("pulse_antinuke", {}).get("enabled", False))

    def test_page_renders_the_chip_manager_and_the_old_200_cap_is_gone(self):
        self.post("zork eins\nzork zwei")
        page = self.client.get("/admin/security").text
        for hook in ('data-term-chips', 'data-input', 'data-add', 'data-list', 'data-filter', 'aria-label="Eigene Wörter"'):
            self.assertIn(hook, page)
        self.assertIn('name="custom_terms"', page, "das Formularfeld bleibt (Fallback ohne JavaScript)")
        self.assertNotIn('maxlength="12000"', page)
        self.assertIn("Enter zum Hinzufügen", page)
        self.assertIn("zork eins\nzork zwei", page)

    def test_chip_script_is_syntactically_valid_javascript(self):
        import re
        import shutil
        import subprocess

        import pulse_guard_admin as guard
        node = shutil.which("node")
        if not node:
            self.skipTest("node nicht installiert")
        script = re.search(r"<script>(.*)</script>", guard.TERM_CHIPS_ASSETS, re.S).group(1)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chips.js"
            path.write_text(script, encoding="utf-8")
            result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unauthorised_users_cannot_change_the_list(self):
        from tests.test_pulse_admin_area import TEAMLER
        with patch.object(ws, "auth", auth_with(TEAMLER)):
            self.assertEqual(self.client.post("/admin/security/profanity", data={"custom_terms": "x y z"}).status_code, 403)
        self.assertFalse(self.config_path.exists())


if __name__ == "__main__":
    unittest.main()
