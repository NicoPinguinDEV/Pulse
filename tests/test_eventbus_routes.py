"""Audit #117: Event-Bus-Routen /api/events/poll und /api/events/stream (Rechte, Cursor, Verbindungsgrenze)."""
import unittest

import pulse_eventbus as bus
import pulse_events as events
from tests.test_teamakte_pages import LEAD, TEAM, Base


class EventBusRouteTests(Base):
    def test_poll_starts_at_the_end_then_delivers_only_new_events_without_payload(self):
        with self.as_user(LEAD):
            first = self.client.get("/api/events/poll").json()
            self.assertEqual(first["events"], [])
            events.emit("task", "completed", target_type="task", target_id="t1", actor_id="1", actor_name="Ann",
                        payload={"title": "geheimer Titel"}, correlation_id="task:t1")
            data = self.client.get(f"/api/events/poll?cursor={first['cursor']}").json()
        self.assertGreater(data["cursor"], first["cursor"])
        self.assertNotIn("geheimer Titel", str(data))

    def test_invalid_cursor_reports_reset(self):
        with self.as_user(TEAM):
            self.assertTrue(self.client.get("/api/events/poll?cursor=abc").json()["reset"])

    def test_denied_without_dashboard_access(self):
        with self.as_user({}):
            self.assertEqual(self.client.get("/api/events/poll").status_code, 403)
            self.assertEqual(self.client.get("/api/events/stream").status_code, 403)

    def test_stream_refuses_when_the_per_user_cap_is_reached(self):
        taken = [bus.acquire_slot("1") for _ in range(bus.MAX_STREAMS_PER_USER)]
        try:
            with self.as_user(TEAM, "1"):
                self.assertEqual(self.client.get("/api/events/stream").status_code, 503)
        finally:
            for ok in taken:
                if ok:
                    bus.release_slot("1")


if __name__ == "__main__":
    unittest.main()
