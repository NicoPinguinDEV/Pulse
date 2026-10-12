import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pulse_support_rules import fit_response, should_answer


class SupportAssistantRuleTests(unittest.TestCase):
    def test_question_mark_triggers_answer(self):
        self.assertTrue(should_answer("Wie bewerbe ich mich?"))

    def test_help_phrases_trigger_answer_without_question_mark(self):
        self.assertTrue(should_answer("Ich brauche Hilfe bei meiner Bewerbung"))
        self.assertTrue(should_answer("Das funktioniert nicht"))
        self.assertTrue(should_answer("Ich habe ein Problem mit der Bewerbung"))
        self.assertTrue(should_answer("Ich brauche Unterstützung"))

    def test_bot_mention_and_reply_trigger_answer(self):
        self.assertTrue(should_answer("Pulse", bot_mentioned=True))
        self.assertTrue(should_answer("Danke", replying_to_bot=True))

    def test_general_requests_without_question_mark_trigger_answer(self):
        self.assertTrue(should_answer("Erstelle mir eine Checkliste für den Dienstantritt"))
        self.assertTrue(should_answer("Schreibe eine freundliche Ticketantwort"))
        self.assertTrue(should_answer("Gib mir Ideen für ein Teamtraining"))

    def test_regular_statement_does_not_trigger_answer(self):
        self.assertFalse(should_answer("Guten Abend zusammen"))
        self.assertFalse(should_answer(""))
        self.assertFalse(should_answer(None))

    def test_response_is_kept_under_discord_limit(self):
        result = fit_response("Antwort " * 1000, max_chars=180)
        self.assertLessEqual(len(result), 180)
        self.assertTrue(result.endswith("…"))

    def test_short_response_is_preserved(self):
        self.assertEqual(fit_response("Alles klar"), "Alles klar")

    def test_invalid_length_is_rejected(self):
        with self.assertRaises(ValueError):
            fit_response("Test", max_chars=1)


if __name__ == "__main__":
    unittest.main()
