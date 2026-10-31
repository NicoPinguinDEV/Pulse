"""Regressions from the VPS log review (2026-10-27): repeated 429 on the absence list, loop stalls."""
import asyncio
import threading
import time
import unittest
from pathlib import Path
from datetime import datetime as real_datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import pulse_interactions as pi
import pulse_jobs
from cogs import abmeldung
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)


def _frozen_datetime(moment):
    class Frozen(real_datetime):
        @classmethod
        def now(cls, tz=None):
            return moment if tz is None else moment.astimezone(tz)
    return Frozen


class AbsenceListPatchTests(unittest.TestCase):
    ROWS = [(1, "Alex", "Urlaub", "01.10.2026", "31.12.2026")]

    def _build(self, moment):
        cog = object.__new__(abmeldung.AbmeldungCog)
        with patch.object(abmeldung, "datetime", _frozen_datetime(moment)):
            return cog._build_embeds(self.ROWS)

    def test_unchanged_list_is_recognised_after_discord_round_trip(self):
        # The 15-minute sync rebuilt the embed with a fresh timestamp, so the "unchanged" check never
        # matched and the same message was PATCHed every run (VPS log: repeated 429 on one message).
        posted = [discord.Embed.from_dict(e.to_dict()) for e in self._build(real_datetime(2026, 10, 26, 11, 43, 39))]
        rebuilt = self._build(real_datetime(2026, 10, 26, 11, 58, 39))
        self.assertEqual(len(posted), len(rebuilt))
        for old, new in zip(posted, rebuilt):
            self.assertEqual(abmeldung._embed_signature(old), abmeldung._embed_signature(new))

    def test_real_content_change_still_patches(self):
        cog = object.__new__(abmeldung.AbmeldungCog)
        before = cog._build_embeds(self.ROWS)[0]
        after = cog._build_embeds(self.ROWS + [(2, "Sam", "Krank", "01.10.2026", "31.12.2026")])[0]
        self.assertNotEqual(abmeldung._embed_signature(before), abmeldung._embed_signature(after))


class UnknownInteractionLogTests(unittest.TestCase):
    def test_expired_interaction_is_logged_briefly_without_reply(self):
        view = pi.PulseView(timeout=None)
        response = SimpleNamespace(status=404, reason="Not Found")
        error = discord.NotFound(response, {"code": 10062, "message": "Unknown interaction"})
        inter = SimpleNamespace(response=SimpleNamespace(is_done=lambda: False, send_message=AsyncMock()),
                                followup=SimpleNamespace(send=AsyncMock()))
        with patch("traceback.print_exception") as tb, patch("builtins.print") as out:
            asyncio.run(view.on_error(inter, error, SimpleNamespace(custom_id="x")))
        tb.assert_not_called()
        inter.response.send_message.assert_not_called()
        inter.followup.send.assert_not_called()
        self.assertIn("abgelaufen", out.call_args[0][0])


class LoopStallWatchdogTests(unittest.TestCase):
    def test_blocking_call_is_reported_with_its_stack(self):
        reports = []

        def blocking_work():
            time.sleep(0.6)

        async def main():
            stop = pulse_jobs.start_stall_watchdog(asyncio.get_running_loop(), threshold=0.2, interval=0.05,
                                                   report=reports.append, cooldown=0)
            await asyncio.sleep(0.1)
            blocking_work()
            await asyncio.sleep(0.2)
            stop.set()

        asyncio.run(main())
        self.assertTrue(reports, "stall was not reported")
        self.assertIn("blocking_work", reports[0])
        self.assertEqual(pulse_jobs.snapshot()["lag"]["stalls"] >= 1, True)

    def test_watchdog_is_started_once(self):
        async def main():
            loop = asyncio.get_running_loop()
            first = pulse_jobs.start_stall_watchdog(loop, report=lambda _t: None)
            second = pulse_jobs.start_stall_watchdog(loop, report=lambda _t: None)
            self.assertIs(first, second)
            first.set()
            await asyncio.sleep(0)
        asyncio.run(main())
        self.assertEqual(sum(t.name == "pulse-loop-watchdog" and t.is_alive() for t in threading.enumerate()) <= 1, True)



class TicketPriorityButtonTests(unittest.TestCase):
    def test_priority_answers_before_the_slow_card_refresh(self):
        # VPS log: 404 Unknown interaction on "Priorität" – the card refresh ran before the response.
        from cogs import tickets
        order = []
        inter = SimpleNamespace(
            channel=SimpleNamespace(id=7), user=SimpleNamespace(id=1, display_name="Team"),
            response=SimpleNamespace(send_message=AsyncMock(side_effect=lambda *a, **k: order.append("response"))),
        )

        async def slow_refresh(*_a, **_k):
            order.append("refresh")
            raise discord.HTTPException(SimpleNamespace(status=500, reason="x"), "boom")

        async def main():
            view = tickets.TicketControlView()
            with patch.object(tickets, "get_ticket", return_value={"id": "T1", "priority": "normal"}),                  patch.object(tickets, "is_team", return_value=True),                  patch.object(tickets, "update_ticket_priority") as update,                  patch.object(tickets, "refresh_ticket_embed", side_effect=slow_refresh),                  patch("builtins.print"):
                await view.priority.callback(inter)
            return update

        update = asyncio.run(main())
        update.assert_called_once()
        self.assertEqual(order, ["response", "refresh"])  # refresh failure does not raise either

    def test_non_team_member_changes_nothing(self):
        from cogs import tickets
        inter = SimpleNamespace(channel=SimpleNamespace(id=7), user=SimpleNamespace(id=2, display_name="X"),
                                response=SimpleNamespace(send_message=AsyncMock()))

        async def main():
            view = tickets.TicketControlView()
            with patch.object(tickets, "get_ticket", return_value={"id": "T1", "priority": "normal"}),                  patch.object(tickets, "is_team", return_value=False),                  patch.object(tickets, "update_ticket_priority") as update,                  patch.object(tickets, "refresh_ticket_embed", new=AsyncMock()) as refresh:
                await view.priority.callback(inter)
            return update, refresh

        update, refresh = asyncio.run(main())
        update.assert_not_called()
        refresh.assert_not_called()


class VoiceSupportReconnectTests(unittest.TestCase):
    def test_dropped_voice_connection_is_rebuilt_before_play(self):
        # VPS log: "ClientException: Not connected to voice" – a dropped client was reused.
        source = Path(abmeldung.__file__).with_name("voice_support.py").read_text(encoding="utf-8")
        start = source.index("async def _start_waiting_audio")
        method = source[start:source.index("async def _continue_waiting_audio")]
        check = method.index("not voice_client.is_connected()")
        self.assertLess(check, method.index("await channel.connect(self_deaf=True)"))
        self.assertLess(check, method.index("voice_client.play("))



class HealthcheckAccessLogTests(unittest.TestCase):
    def _record(self, path, status):
        import logging
        return logging.LogRecord("uvicorn.access", 20, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                                 ("127.0.0.1:1", "GET", path, "1.1", status), None)

    def test_successful_probe_is_dropped_but_failures_and_real_requests_stay(self):
        import pulse_logging
        f = pulse_logging.HealthcheckAccessFilter()
        self.assertFalse(f.filter(self._record("/healthz", 200)))
        self.assertTrue(f.filter(self._record("/healthz", 503)))   # failed probe stays visible
        self.assertTrue(f.filter(self._record("/admin", 200)))
        self.assertTrue(f.filter(self._record("/healthzx", 200)))

    def test_filter_is_installed_once(self):
        import logging
        import pulse_logging
        access = logging.getLogger("uvicorn.access")
        before = list(access.filters)
        try:
            pulse_logging.quiet_healthcheck_access_log()
            pulse_logging.quiet_healthcheck_access_log()
            self.assertEqual(sum(isinstance(f, pulse_logging.HealthcheckAccessFilter) for f in access.filters), 1)
        finally:
            access.filters[:] = before


class SocialPromoPersistentFailureLogTests(unittest.TestCase):
    def _poll(self, failures, logged_hours_ago):
        from datetime import timedelta, timezone
        from cogs import social_promo
        cog = object.__new__(social_promo.SocialPromo)
        logged = (real_datetime.now(timezone.utc) - timedelta(hours=logged_hours_ago)).isoformat(timespec="seconds")
        source = {"name": "Bochum RP V1", "feed_url": "https://example.org/feed", "platform": "tiktok",
                  "feed_failures": failures, "last_error_logged_at": logged, "initialized": True}
        cog.sources = {"s1": source}
        cog._fetch_feed = AsyncMock(side_effect=ValueError("Keine RSSHub-Instanz"))
        cog._save_sources = lambda: None
        with patch("builtins.print") as out:
            asyncio.run(cog._poll_one("s1", source))
        return out, source

    def test_new_failure_is_still_logged_hourly(self):
        out, source = self._poll(failures=1, logged_hours_ago=2)
        out.assert_called_once()
        self.assertEqual(source["feed_failures"], 2)

    def test_persistent_failure_is_logged_only_every_six_hours(self):
        out, source = self._poll(failures=6, logged_hours_ago=2)
        out.assert_not_called()
        self.assertEqual(source["feed_failures"], 7)          # still counted and retried
        self.assertIn("retry_after", source)
        out, _ = self._poll(failures=6, logged_hours_ago=7)
        out.assert_called_once()
        self.assertIn("7. Fehlversuch", out.call_args[0][0])



class DashboardRenderingTests(unittest.TestCase):
    def test_ultimate_page_runs_bare_javascript_instead_of_printing_it(self):
        # /ultimate/system passed bare JS: it was shown as page text and the live monitor never loaded.
        import pulse_ultimate
        captured = {}
        with patch("pulse_design.legacy_page", side_effect=lambda *a, **k: captured.update(k) or "ok"):
            pulse_ultimate.page(SimpleNamespace(), "system", "T", "S", "<p>body</p>", "loadPulseMonitor();")
            self.assertEqual(captured["scripts"], "<script>loadPulseMonitor();</script>")
            pulse_ultimate.page(SimpleNamespace(), "system", "T", "S", "<p>body</p>", "<script>x()</script>")
            self.assertEqual(captured["scripts"], "<script>x()</script>")   # already wrapped: unchanged
            pulse_ultimate.page(SimpleNamespace(), "system", "T", "S", "<p>body</p>")
            self.assertEqual(captured["scripts"], "")

    def test_mobile_layout_rules_keep_pages_within_the_phone_width(self):
        css = Path(pulse_jobs.__file__).with_name("pulse_pro.py").read_text(encoding="utf-8")
        # Calendar: the minimum width belongs inside the scroll box, not on its parent section.
        self.assertNotIn(".calendar-main{min-width:680px}", css)
        self.assertIn(".calendar-scroll>.cal-weekdays,.calendar-scroll>.cal-grid{min-width:650px}", css)
        self.assertIn(".calendar-shell>*{min-width:0}", css)
        # Operations Center top bar: .pulse-btn's display:flex overrode Tailwind's "hidden" on phones.
        self.assertIn("@media(max-width:639px){.pulse-topbar .pulse-btn.hidden{display:none}}", css)


if __name__ == "__main__":
    unittest.main()
