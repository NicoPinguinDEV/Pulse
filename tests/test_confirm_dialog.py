"""K-09: confirmations answer before the work, run once, re-check permissions and expire visibly."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs import pulse_suite


class FakeResponse:
    def __init__(self, log):
        self.log, self.done = log, False

    def is_done(self):
        return self.done

    async def edit_message(self, **kwargs):
        self.done = True
        self.log.append(("edit_message", kwargs.get("content")))

    async def send_message(self, content, ephemeral=False):
        self.done = True
        self.log.append(("send_message", content))


class FakeInteraction:
    def __init__(self, log, user_id=1):
        self.user = SimpleNamespace(id=user_id)
        self.log = log
        self.response = FakeResponse(log)
        self.followup = SimpleNamespace(send=self._followup)

    async def edit_original_response(self, **kwargs):
        self.log.append(("edit_original", kwargs.get("content")))

    async def _followup(self, content, ephemeral=False):
        self.log.append(("followup", content))


def run(coro):
    return asyncio.run(coro)


class ConfirmTests(unittest.TestCase):
    def setUp(self):
        self.patches = [patch.object(pulse_suite, "allowed", lambda *a: True),
                        patch.object(pulse_suite, "locked", lambda: False),
                        patch("builtins.print")]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()

    def test_answer_comes_before_slow_work_and_result_edits_it(self):
        log, calls = [], []

        async def work(x):
            await asyncio.sleep(0.01)  # slow Discord calls (rate limits)
            calls.append(1)
            await x.response.edit_message(content="✅ Warnung erstellt", embed=None, view=None)

        async def scenario():
            view = pulse_suite.Confirm(1, work, key="warn")
            i = FakeInteraction(log)
            await view.yes.callback(i)
            # double click on the same dialog does not run the action again
            await view.yes.callback(FakeInteraction(log))
        run(scenario())
        self.assertEqual(log[0], ("edit_message", "⏳ Wird ausgeführt …"))
        self.assertEqual(log[1], ("edit_original", "✅ Warnung erstellt"))
        self.assertEqual(calls, [1])
        self.assertEqual(log[-1], ("send_message", "⏳ Wird bereits ausgeführt."))

    def test_permissions_are_checked_again_on_click(self):
        log, calls = [], []

        async def work(x):
            calls.append(1)
        with patch.object(pulse_suite, "allowed", lambda *a: False):
            run(pulse_suite.Confirm(1, work, key="warn").yes.callback(FakeInteraction(log)))
        self.assertEqual(calls, [])
        self.assertIn("Abgebrochen", log[0][1])

    def test_unexpected_error_shows_error_id_not_exception_text(self):
        log = []

        async def work(x):
            raise RuntimeError("geheime interna")
        run(pulse_suite.Confirm(1, work, key="warn").yes.callback(FakeInteraction(log)))
        self.assertIn("Fehler-ID", log[-1][1])
        self.assertNotIn("geheime interna", log[-1][1])

    def test_other_user_cannot_confirm_and_timeout_disables(self):
        log, calls = [], []

        async def work(x):
            calls.append(1)
        origin = FakeInteraction(log)
        view = pulse_suite.Confirm(1, work, key="warn", origin=origin)
        run(view.yes.callback(FakeInteraction(log, user_id=2)))
        self.assertEqual(calls, [])
        run(view.on_timeout())
        self.assertEqual(log[-1], ("edit_original", "⌛ Bestätigung abgelaufen – es wurde nichts ausgeführt."))


if __name__ == "__main__":
    unittest.main()
