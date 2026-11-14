"""Audit #117: event bus (cursor polling fallback, SSE stream, per-viewer visibility, limits)."""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_eventbus as bus
import pulse_events as ev

ADMIN = {"is_admin": True}
TICKET = {"can_manage_tickets": True}
MOD = {"can_moderate_kicks": True}
NOBODY = {"can_view_dashboard": True}


class BusTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.patch.start()
        pulse_db._SCHEMA_READY.discard(self.db_path)
        pulse_db.init_db()

    def tearDown(self):
        pulse_db._SCHEMA_READY.discard(self.db_path)
        self.patch.stop()
        self.tmp.cleanup()


class VisibilityTests(unittest.TestCase):
    def item(self, domain, case_ref=""):
        return {"domain": domain, "case_ref": case_ref}

    def test_domain_rights(self):
        self.assertTrue(bus.visible_to(self.item("ticket"), TICKET))
        self.assertFalse(bus.visible_to(self.item("ticket"), MOD))
        self.assertFalse(bus.visible_to(self.item("ticket"), NOBODY))
        self.assertTrue(bus.visible_to(self.item("moderation"), MOD))

    def test_unlisted_domains_are_admin_only(self):
        for domain in ("privacy", "antinuke", "automation", "panel", "system", "other"):
            self.assertTrue(bus.visible_to(self.item(domain), ADMIN), domain)
            self.assertFalse(bus.visible_to(self.item(domain), {**TICKET, **MOD}), domain)

    def test_case_ref_needs_its_own_case_right(self):
        self.assertTrue(bus.visible_to(self.item("moderation", "BM:MOD-1A2B3C"), MOD))
        # task right alone must not reveal an event tied to a moderation case
        self.assertFalse(bus.visible_to(self.item("task", "BM:MOD-1A2B3C"), {"can_manage_tasks": True}))
        self.assertTrue(bus.visible_to(self.item("task", "BM:MOD-1A2B3C"), {"can_manage_tasks": True, **MOD}))
        self.assertFalse(bus.visible_to(self.item("task", "garbage"), {"can_manage_tasks": True}))

    def test_fail_closed(self):
        for perms in (None, {}, [], "is_admin"):
            self.assertFalse(bus.visible_to(self.item("ticket"), perms))


class PollTests(BusTestCase):
    def test_first_poll_starts_live_without_replay(self):
        ev.emit("ticket", "closed", target_id="T1")
        first = bus.poll(ADMIN)
        self.assertEqual(first["events"], [])
        ev.emit("ticket", "closed", target_id="T2")
        nxt = bus.poll(ADMIN, first["cursor"])
        self.assertEqual([e["target_id"] for e in nxt["events"]], ["T2"])

    def test_cursor_progress_without_duplicates_or_gaps(self):
        cursor = bus.poll(ADMIN)["cursor"]
        for i in range(5):
            ev.emit("ticket", "closed", target_id=f"T{i}")
        a = bus.poll(ADMIN, cursor, limit=2)
        b = bus.poll(ADMIN, a["cursor"], limit=2)
        c = bus.poll(ADMIN, b["cursor"], limit=2)
        ids = [e["target_id"] for e in a["events"] + b["events"] + c["events"]]
        self.assertEqual(ids, ["T0", "T1", "T2", "T3", "T4"])
        self.assertEqual(bus.poll(ADMIN, c["cursor"])["events"], [])

    def test_legacy_record_event_rows_are_delivered_too(self):
        cursor = bus.poll(ADMIN)["cursor"]
        pulse_db.record_event("ticket_closed", "ticket", "T9", "7", "Mod", {"reason": "x"})
        got = bus.poll(TICKET, cursor)["events"]
        self.assertEqual([(e["domain"], e["target_id"]) for e in got], [("ticket", "T9")])

    def test_viewer_never_sees_hidden_events_but_cursor_moves_on(self):
        cursor = bus.poll(ADMIN)["cursor"]
        ev.emit("privacy", "received", target_id="P1", payload={"secret": "Name"})
        ev.emit("ticket", "closed", target_id="T1")
        got = bus.poll(TICKET, cursor)
        self.assertEqual([e["target_id"] for e in got["events"]], ["T1"])
        self.assertGreater(got["cursor"], cursor)
        self.assertEqual(bus.poll(TICKET, got["cursor"])["events"], [])
        self.assertEqual(bus.poll(NOBODY, cursor)["events"], [])

    def test_public_view_has_no_payload_or_actor(self):
        cursor = bus.poll(ADMIN)["cursor"]
        ev.emit("moderation", "created", target_id="42", actor_id="7", actor_name="Geheimer Mod",
                payload={"reason": "vertraulich"}, case_ref="BM:MOD-1A2B3C")
        item = bus.poll(ADMIN, cursor)["events"][0]
        blob = json.dumps(item, ensure_ascii=False)
        self.assertNotIn("vertraulich", blob)
        self.assertNotIn("Geheimer Mod", blob)
        self.assertEqual(item["url"], "/mod-cases/verlauf?ref=BM%3AMOD-1A2B3C")
        self.assertEqual(item["correlation_id"], "BM:MOD-1A2B3C")

    def test_cursor_beyond_end_resets_instead_of_waiting_forever(self):
        ev.emit("ticket", "closed", target_id="T1")
        got = bus.poll(ADMIN, 10_000)
        self.assertTrue(got["reset"])
        self.assertEqual(got["events"], [])
        self.assertLess(got["cursor"], 10_000)
        self.assertTrue(bus.poll(ADMIN, "abc")["reset"])

    def test_limit_is_bounded(self):
        cursor = bus.poll(ADMIN)["cursor"]
        for i in range(4):
            ev.emit("ticket", "closed", target_id=f"T{i}")
        self.assertEqual(len(bus.poll(ADMIN, cursor, limit=-3)["events"]), 1)


class StreamTests(BusTestCase):
    def run_stream(self, perms, cursor, script, *, max_seconds=5.0):
        """Drive the generator with a fake clock; ``script(step)`` may write events between steps."""
        state = {"t": 0.0, "step": 0}

        def clock():
            return state["t"]

        async def sleep(seconds):
            state["t"] += seconds
            state["step"] += 1
            script(state["step"])

        async def never_disconnected():
            return False

        async def collect():
            frames = []
            async for frame in bus.stream(perms, cursor, never_disconnected, max_seconds=max_seconds,
                                          interval=1.0, sleep=sleep, clock=clock):
                frames.append(frame)
            return frames
        return asyncio.run(collect())

    def test_stream_delivers_new_event_then_expires_with_cursor(self):
        def script(step):
            if step == 2:
                ev.emit("ticket", "closed", target_id="T1")
        frames = self.run_stream(TICKET, None, script)
        text = "".join(frames)
        self.assertTrue(text.startswith("retry: 3000"))
        self.assertIn("event: ready", text)
        self.assertEqual(text.count("event: pulse"), 1)
        self.assertIn('"target_id":"T1"', text)
        self.assertTrue(frames[-1].startswith("id: ") and "event: expired" in frames[-1])

    def test_stream_catches_legacy_writers_via_interval(self):
        def script(step):
            if step == 3:
                pulse_db.record_event("ticket_closed", "ticket", "T7", "7", "Mod", {})
        text = "".join(self.run_stream(TICKET, None, script))
        self.assertIn('"target_id":"T7"', text)

    def test_stream_hides_events_and_emits_heartbeat(self):
        def script(step):
            if step == 2:
                ev.emit("privacy", "received", target_id="P1")
        text = "".join(self.run_stream(TICKET, None, script, max_seconds=40.0))
        self.assertNotIn("P1", text)
        self.assertNotIn("event: pulse", text)
        self.assertIn(": ping", text)

    def test_resume_with_last_event_id_replays_missed_events_once(self):
        cursor = bus.poll(ADMIN)["cursor"]
        ev.emit("ticket", "closed", target_id="T1")
        ev.emit("ticket", "closed", target_id="T2")
        text = "".join(self.run_stream(TICKET, cursor, lambda step: None, max_seconds=2.0))
        self.assertEqual(text.count('"target_id":"T1"'), 1)
        self.assertEqual(text.count('"target_id":"T2"'), 1)

    def test_stream_stops_when_client_disconnects(self):
        async def run():
            calls = {"n": 0}

            async def disconnected():
                calls["n"] += 1
                return calls["n"] > 1

            async def fast(_):
                return None
            return [f async for f in bus.stream(ADMIN, None, disconnected, sleep=fast)]
        frames = asyncio.run(run())
        self.assertFalse(any("expired" in f for f in frames))

    def test_stale_cursor_streams_reset(self):
        text = "".join(self.run_stream(ADMIN, 99999, lambda step: None, max_seconds=1.0))
        self.assertIn("event: reset", text)


class SlotTests(unittest.TestCase):
    def tearDown(self):
        for uid in list(bus._slots_user):
            while uid in bus._slots_user:
                bus.release_slot(uid)

    def test_per_user_and_global_caps(self):
        for _ in range(bus.MAX_STREAMS_PER_USER):
            self.assertTrue(bus.acquire_slot("u1"))
        self.assertFalse(bus.acquire_slot("u1"))
        bus.release_slot("u1")
        self.assertTrue(bus.acquire_slot("u1"))
        with patch.object(bus, "MAX_STREAMS_TOTAL", bus._slots_total):
            self.assertFalse(bus.acquire_slot("someone-else"))

    def test_release_is_safe_when_nothing_held(self):
        bus.release_slot("ghost")
        self.assertEqual(bus._slots_total, 0)


class WakeTests(BusTestCase):
    def test_emit_bumps_version_and_survives_bus_failure(self):
        before = bus._current_version()
        ev.emit("ticket", "closed")
        self.assertEqual(bus._current_version(), before + 1)
        with patch.object(bus, "bump", side_effect=RuntimeError("x")):
            self.assertIsNotNone(ev.emit("ticket", "closed"))  # event is stored even if wake-up fails

    def test_sse_frame_format(self):
        self.assertEqual(bus.sse_frame("pulse", {"a": "ä"}, 5), 'id: 5\nevent: pulse\ndata: {"a":"ä"}\n\n')


if __name__ == "__main__":
    unittest.main()
