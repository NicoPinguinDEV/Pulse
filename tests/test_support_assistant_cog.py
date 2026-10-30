"""Cog-level tests for the AI support chat with a fake HTTP client (no network, no Discord)."""
import asyncio
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pulse_ai_resilience  # noqa: E402
from cogs import support_assistant as sa  # noqa: E402

ENV = {
    "PULSE_SUPPORT_ENABLED": "true",
    "GEMINI_API_KEY": "test-key",
    "PULSE_SUPPORT_CHANNEL_ID": "111",
}


def ok(text, finish="stop"):
    return httpx.Response(200, json={"choices": [{"message": {"content": text}, "finish_reason": finish}]})


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    async def post(self, endpoint, headers=None, json=None):
        self.payloads.append(json)
        return self.responses.pop(0)

    async def aclose(self):
        pass


def make_cog(responses, **env):
    with mock.patch.dict(os.environ, {**ENV, **env}, clear=False):
        for name in ("PULSE_SUPPORT_PROVIDER", "OPENAI_API_KEY", "PULSE_SUPPORT_FALLBACK_MODELS",
                     "PULSE_SUPPORT_REASONING_EFFORT", "GEMINI_MODEL"):
            if name not in env:
                os.environ.pop(name, None)
        cog = sa.SupportAssistant(SimpleNamespace(user=SimpleNamespace(id=999)))
    asyncio.run(cog._http_client.aclose())
    cog._http_client = FakeClient(responses)
    return cog


class FakeChannel:
    def __init__(self, channel_id=111):
        self.id = channel_id
        self.sent = []

    async def send(self, content, **kwargs):
        self.sent.append(content)

    def typing(self):
        channel = self

        class _Typing:
            async def __aenter__(self):
                return channel

            async def __aexit__(self, *exc):
                return False

        return _Typing()


class FakeMessage:
    def __init__(self, content, channel=None, author_id=5):
        self.clean_content = content
        self.content = content
        self.channel = channel or FakeChannel()
        self.author = SimpleNamespace(id=author_id, bot=False, roles=[])
        self.guild = SimpleNamespace(id=1)
        self.webhook_id = None
        self.mentions = []
        self.reference = None
        self.replies = []

    async def reply(self, content, **kwargs):
        self.replies.append(content)


MESSAGES = [{"role": "user", "content": "Hallo?"}]


class SupportAssistantCogTests(unittest.TestCase):
    def setUp(self):
        pulse_ai_resilience._STATES.clear()

    def test_gemini_key_without_provider_is_used(self):
        cog = make_cog([])
        self.assertEqual(cog.provider, "gemini")
        self.assertEqual(cog.api_key, "test-key")

    def test_reasoning_effort_is_sent_for_gemini(self):
        cog = make_cog([ok("Antwort")])
        self.assertEqual(asyncio.run(cog._request_completion(MESSAGES)), "Antwort")
        self.assertEqual(cog._http_client.payloads[0]["reasoning_effort"], "low")
        self.assertEqual(cog._http_client.payloads[0]["max_tokens"], cog.max_tokens)

    def test_unknown_model_falls_back_and_is_remembered(self):
        cog = make_cog([
            httpx.Response(404, json={"error": {"message": "models/gemini-3.1-flash-lite is not found"}}),
            ok("Ersatz"),
            ok("Wieder Ersatz"),
        ])
        self.assertEqual(asyncio.run(cog._request_completion(MESSAGES)), "Ersatz")
        self.assertEqual(cog.active_model, cog.models[1])
        self.assertEqual(cog._provider_pause_seconds(), 0)
        asyncio.run(cog._request_completion(MESSAGES))
        self.assertEqual(cog._http_client.payloads[-1]["model"], cog.models[1])

    def test_rejected_reasoning_parameter_is_dropped(self):
        cog = make_cog([
            httpx.Response(400, json={"error": {"message": "Unrecognized request argument supplied: reasoning_effort"}}),
            ok("Ohne Reasoning"),
        ])
        self.assertEqual(asyncio.run(cog._request_completion(MESSAGES)), "Ohne Reasoning")
        self.assertNotIn("reasoning_effort", cog._http_client.payloads[1])
        self.assertIsNone(cog.reasoning_effort)

    def test_empty_thinking_answer_has_actionable_diagnostic(self):
        cog = make_cog([ok(None, finish="length")])
        self.assertIsNone(asyncio.run(cog._request_completion(MESSAGES)))
        self.assertIn("finish_reason=length", cog._last_api_error)

    def test_per_minute_rate_limit_pauses_briefly(self):
        body = {"error": {"message": "Quota exceeded per minute", "details": [{"retryDelay": "12s"}]}}
        cog = make_cog([httpx.Response(429, json=body)])
        self.assertIsNone(asyncio.run(cog._request_completion(MESSAGES)))
        pause = cog._provider_pause_seconds()
        self.assertGreater(pause, 10)
        self.assertLessEqual(pause, 31)

    def test_exhausted_quota_still_pauses_long(self):
        cog = make_cog([httpx.Response(429, json={"error": {"code": "insufficient_quota"}})])
        asyncio.run(cog._request_completion(MESSAGES))
        self.assertGreaterEqual(cog._provider_pause_seconds(), 299)

    def test_offline_answer_while_provider_paused(self):
        cog = make_cog([])
        cog._api_paused_until = time.monotonic() + 60
        message = FakeMessage("Was gilt in Safezones?")
        asyncio.run(cog.on_message(message))
        self.assertEqual(cog._http_client.payloads, [])  # no AI request during the pause
        self.assertEqual(len(message.replies), 1)
        self.assertIn("Wissensbasis", message.replies[0])
        self.assertIn("Krankenhaus", message.replies[0])
        self.assertEqual(cog._stats["offline_answers"], 1)

    def test_offline_fallback_can_be_disabled(self):
        cog = make_cog([], PULSE_SUPPORT_OFFLINE_FALLBACK="false")
        cog._api_paused_until = time.monotonic() + 60
        message = FakeMessage("Was gilt in Safezones?")
        asyncio.run(cog.on_message(message))
        self.assertEqual(len(message.replies), 1)
        self.assertIn("nicht erreichbar", message.replies[0])

    def test_disabled_assistant_never_answers_from_knowledge(self):
        cog = make_cog([], PULSE_SUPPORT_ENABLED="false")
        message = FakeMessage("Was gilt in Safezones?")
        asyncio.run(cog.on_message(message))
        self.assertIn("deaktiviert", message.replies[0])
        self.assertEqual(cog._stats["offline_answers"], 0)

    def test_long_public_answer_is_split_not_cut(self):
        long_text = "\n".join(f"Schritt {i}: " + "x" * 80 for i in range(30))
        cog = make_cog([ok(long_text)])
        message = FakeMessage("Wie funktioniert das?")
        asyncio.run(cog.on_message(message))
        self.assertEqual(len(message.replies), 1)
        self.assertGreaterEqual(len(message.channel.sent), 1)
        combined = message.replies[0] + "".join(message.channel.sent)
        self.assertIn("Schritt 29", combined)
        self.assertIn("KI-Support von Pulse", message.channel.sent[-1])
        for part in [message.replies[0], *message.channel.sent]:
            self.assertLessEqual(len(part), 2000)

    def test_ki_vergessen_only_clears_own_history(self):
        cog = make_cog([])
        cog._history[(111, 5)] = [{"role": "user", "content": "a"}]
        cog._history[(222, 5)] = [{"role": "user", "content": "b"}]
        cog._history[(111, 6)] = [{"role": "user", "content": "c"}]
        sent = []

        async def send_message(text, **kwargs):
            sent.append((text, kwargs))

        interaction = SimpleNamespace(user=SimpleNamespace(id=5), response=SimpleNamespace(send_message=send_message))
        asyncio.run(cog.ki_vergessen.callback(cog, interaction))
        self.assertEqual(list(cog._history), [(111, 6)])
        self.assertTrue(sent[0][1]["ephemeral"])


if __name__ == "__main__":
    unittest.main()
