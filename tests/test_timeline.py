"""U-10: chronological team-file timeline with E-3 visibility applied in the builder itself."""
import unittest

import pulse_timeline as tl


def data():
    return dict(
        warns=[{"date": "01.10.2026 10:00", "reason": "Zu spät", "by": "Leitung", "revoked_at": "05.10.2026 09:00",
                "revoked_by": "Admin", "revoked_reason": "Irrtum"}],
        shifts=[{"started_at_iso": "2026-10-03T18:00:00+02:00", "ended_at_iso": "2026-10-03T20:30:00+02:00", "duration_seconds": 9000}],
        tickets=[{"id": "T-1", "category": "Support", "status": "closed", "opened_at": "2026-10-02T12:00:00+00:00",
                  "claimed_by_id": "7"}],
        tasks=[{"title": "Schulung vorbereiten", "status": "done", "created_at": "2026-10-04T08:00:00+00:00"}],
        events=[{"event_type": "promotion_approved", "actor_name": "Admin", "created_at": "2026-10-06T08:00:00+00:00"}],
        notes=["[07.10.2026] Sehr zuverlässig (von Leitung)", "uralte Notiz ohne Datum"],
    )


class TimelineTests(unittest.TestCase):
    def test_newest_first_with_all_kinds_for_authorised_viewer(self):
        items = tl.build(**data(), show_warns=True, show_notes=True, user_id="7")
        self.assertEqual([i["kind"] for i in items[:3]], ["note", "event", "warn"])  # 07.10, 06.10, 05.10 (revocation)
        self.assertEqual(items[-1]["detail"], "uralte Notiz ohne Datum")             # undated at the end
        self.assertIn("2 h 30 min", next(i["title"] for i in items if i["kind"] == "shift"))
        self.assertIn("übernommen", next(i["title"] for i in items if i["kind"] == "ticket"))

    def test_e3_hides_warnings_and_notes(self):
        items = tl.build(**data(), show_warns=False, show_notes=False, user_id="7")
        kinds = {i["kind"] for i in items}
        self.assertNotIn("warn", kinds)
        self.assertNotIn("note", kinds)
        html = tl.render(items, "/member/7")
        self.assertNotIn("Zu spät", html)
        self.assertNotIn("zuverlässig", html)

    def test_events_about_warnings_follow_e3(self):
        d = data()
        d["events"] = [{"event_type": "warning_created", "created_at": "2026-10-06T08:00:00+00:00"},
                       {"event_type": "team_high_impact_warning_blocked", "created_at": "2026-10-06T09:00:00+00:00"},
                       {"event_type": "promotion_decided", "created_at": "2026-10-06T10:00:00+00:00"}]
        hidden = [i["title"] for i in tl.build(**d, show_warns=False, show_notes=False, user_id="7") if i["kind"] == "event"]
        self.assertEqual(hidden, ["promotion decided"])
        shown = [i for i in tl.build(**d, show_warns=True, show_notes=True, user_id="7") if i["kind"] == "event"]
        self.assertEqual(len(shown), 3)

    def test_render_filters_and_escapes(self):
        d = data()
        d["tasks"] = [{"title": "<script>x</script>", "created_at": "2026-10-04T08:00:00+00:00"}]
        items = tl.build(**d, show_warns=True, show_notes=True, user_id="7")
        html = tl.render(items, "/member/7", selected="task")
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("Zu spät", html)            # filtered to tasks
        self.assertIn("/member/7?tl=warn", html)     # chips for the other kinds
        self.assertIn('class="pulse-pill good"', html)
        self.assertIn("Noch nichts", tl.render([], "/member/7"))
        notes = tl.render(items, "/member/7", selected="note")
        self.assertIn("07.10.2026 · Sehr", notes)   # a day without a fake 00:00
        self.assertNotIn("00:00", notes)


if __name__ == "__main__":
    unittest.main()
