import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pulse_ai_client as ai  # noqa: E402


class ProviderConfigTests(unittest.TestCase):
    def test_explicit_provider_wins(self):
        self.assertEqual(ai.resolve_provider({"PULSE_SUPPORT_PROVIDER": "OpenAI", "GEMINI_API_KEY": "x"}), "openai")

    def test_gemini_key_alone_selects_gemini(self):
        # Regression: the chat defaulted to OpenAI and reported "not configured".
        self.assertEqual(ai.resolve_provider({"GEMINI_API_KEY": "x"}), "gemini")

    def test_openai_key_alone_selects_openai(self):
        self.assertEqual(ai.resolve_provider({"OPENAI_API_KEY": "x"}), "openai")

    def test_free_provider_is_default(self):
        self.assertEqual(ai.resolve_provider({}), "gemini")
        self.assertEqual(ai.resolve_provider({"PULSE_SUPPORT_PROVIDER": "bogus"}), "gemini")

    def test_model_chain(self):
        chain = ai.model_chain("gemini", "gemini-3.1-flash-lite", None)
        self.assertEqual(chain[0], "gemini-3.1-flash-lite")
        self.assertIn("gemini-3.1-flash-lite-preview", chain)
        self.assertEqual(ai.model_chain("gemini", "a", "b, a ,c"), ["a", "b", "c"])
        self.assertEqual(ai.model_chain("gemini", "a", "off"), ["a"])
        self.assertEqual(ai.model_chain("openai", "gpt-4.1-mini", ""), ["gpt-4.1-mini"])

    def test_reasoning_effort(self):
        self.assertEqual(ai.resolve_reasoning_effort("gemini", None), "low")
        self.assertIsNone(ai.resolve_reasoning_effort("openai", None))
        self.assertEqual(ai.resolve_reasoning_effort("openai", "minimal"), "minimal")
        self.assertIsNone(ai.resolve_reasoning_effort("gemini", "off"))


class ResponseParsingTests(unittest.TestCase):
    def test_string_and_part_content(self):
        self.assertEqual(ai.extract_completion({"choices": [{"message": {"content": " Hi "}, "finish_reason": "stop"}]}), ("Hi", "stop"))
        parts = {"choices": [{"message": {"content": [{"type": "text", "text": "A"}, "B"]}}]}
        self.assertEqual(ai.extract_completion(parts)[0], "AB")

    def test_empty_thinking_answer_reports_length(self):
        data = {"choices": [{"message": {"content": None}, "finish_reason": "length"}]}
        self.assertEqual(ai.extract_completion(data), ("", "length"))

    def test_malformed_responses(self):
        for data in (None, [], {}, {"choices": []}, {"choices": ["x"]}):
            self.assertEqual(ai.extract_completion(data), ("", ""))


class ProviderErrorTests(unittest.TestCase):
    def test_gemini_rate_limit_with_retry_delay(self):
        body = [{"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "Quota exceeded per minute",
                           "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "23s"}]}}]
        err = ai.parse_provider_error(429, body)
        self.assertEqual(err.retry_delay, 23)
        self.assertFalse(err.quota_exhausted)

    def test_daily_quota_is_long_pause(self):
        body = {"error": {"message": "Quota exceeded", "details": [
            {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}
        self.assertTrue(ai.parse_provider_error(429, body).quota_exhausted)
        self.assertTrue(ai.parse_provider_error(429, {"error": {"code": "insufficient_quota"}}).quota_exhausted)

    def test_model_and_reasoning_errors(self):
        self.assertTrue(ai.parse_provider_error(404, {"error": {"message": "models/x is not found"}}).model_not_found)
        self.assertTrue(ai.parse_provider_error(400, {"error": {"message": "The model `x` does not exist"}}).model_not_found)
        self.assertFalse(ai.parse_provider_error(400, {"error": {"message": "bad request"}}).model_not_found)
        self.assertTrue(ai.parse_provider_error(400, {"error": {"message": "Unrecognized request argument: reasoning_effort"}}).reasoning_rejected)
        self.assertEqual(ai.parse_provider_error(500, "garbage").status_code, 500)


class OfflineKnowledgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.knowledge = (ROOT / "support_knowledge.md").read_text(encoding="utf-8")
        cls.templates = (ROOT / "team_ki_vorlagen.md").read_text(encoding="utf-8")

    def test_finds_rules_by_topic_and_paragraph(self):
        self.assertIn("Krankenhaus", ai.offline_answer("Was gilt in Safezones?", self.knowledge))
        self.assertIn("Geisel", ai.offline_answer("Was steht in §11?", self.knowledge))
        self.assertIn("Peacetime", ai.offline_answer("Was ist Peacetime", self.knowledge))

    def test_small_talk_and_general_questions_get_no_excerpt(self):
        for question in ("Hallo wie geht es dir?", "Wie spät ist es", "Kannst du mir ein Gedicht schreiben?"):
            self.assertIsNone(ai.offline_answer(question, self.knowledge), question)

    def test_team_templates_and_numbered_broadcasts(self):
        answer = ai.offline_answer("Vorlage Partnerschaft abgelehnt", self.knowledge, self.templates)
        self.assertIn("abgelehnt", answer)
        self.assertIn("Broadcast 08", ai.offline_answer("Broadcast 08", self.knowledge, self.templates))

    def test_answer_respects_length_budget(self):
        answer = ai.offline_answer("Was steht in §11?", self.knowledge, max_chars=250)
        self.assertLessEqual(len(answer), 260)


if __name__ == "__main__":
    unittest.main()
