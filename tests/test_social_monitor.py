"""Audit #54: Social-Media-Monitor - Filter, Veröffentlichungsübersicht und Protokoll (temp DB, kein Netzwerk, keine Discord-Verbindung)."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pulse_db
import pulse_social_log as slog
import webserver as ws
from tests.test_wiki_plus import Base as WikiBase

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
MANAGER = {"can_view_dashboard": True, "can_manage_announcements": True}
TEAM = {"can_view_dashboard": True}

SOURCES = {
    "s1": {"id": "s1", "name": "Marvin Gaming", "platform": "youtube", "enabled": True, "initialized": True, "feed_status": "active", "announced_count": 2},
    "s2": {"id": "s2", "name": "Tik Tanja", "platform": "tiktok", "enabled": True, "initialized": True, "feed_status": "degraded"},
    "s3": {"id": "s3", "name": "Pausierter Kanal", "platform": "youtube", "enabled": False, "initialized": True, "feed_status": "active"},
    "s4": {"id": "s4", "name": "Neu", "platform": "twitch", "enabled": True, "initialized": False, "feed_status": "pending", "custom_name": "Besonderer Name"},
}


class FilterTests(unittest.TestCase):
    def names(self, **kw):
        return [s["name"] for s in slog.filter_sources(SOURCES.values(), **kw)]

    def test_status_mapping(self):
        self.assertEqual({k: slog.source_status(v) for k, v in SOURCES.items()}, {"s1": "aktiv", "s2": "gestoert", "s3": "pausiert", "s4": "wartet"})

    def test_filters_combine(self):
        self.assertEqual(len(self.names()), 4)
        self.assertEqual(self.names(platform="youtube"), ["Marvin Gaming", "Pausierter Kanal"])
        self.assertEqual(self.names(platform="YouTube", status="aktiv"), ["Marvin Gaming"])
        self.assertEqual(self.names(status="gestoert"), ["Tik Tanja"])
        self.assertEqual(self.names(q="besonderer"), ["Neu"])
        self.assertEqual(self.names(q="marvin", platform="tiktok"), [])
        self.assertEqual(self.names(status="unbekannt"), ["Marvin Gaming", "Tik Tanja", "Pausierter Kanal", "Neu"])   # unbekannter Status filtert nicht
        self.assertEqual(slog.filter_sources([None, "x", {"name": "ok"}]), [{"name": "ok"}])


class LogTests(WikiBase):
    def test_record_summary_and_window(self):
        item = {"title": "Neues Video", "url": "https://youtu.be/abc"}
        self.assertTrue(slog.record(SOURCES["s1"], item, NOW - timedelta(days=1)))
        self.assertTrue(slog.record(SOURCES["s1"], {"title": "Zweites", "url": "https://youtu.be/def"}, NOW - timedelta(days=2)))
        self.assertTrue(slog.record(SOURCES["s2"], {"title": "Clip", "url": "https://tiktok.com/x"}, NOW - timedelta(days=3)))
        self.assertTrue(slog.record(SOURCES["s2"], {"title": "Alt"}, NOW - timedelta(days=60)))
        summary = slog.summary(30, NOW)
        self.assertEqual((summary["total"], summary["by_platform"]), (3, {"youtube": 2, "tiktok": 1}))
        self.assertEqual(summary["by_source"][0], ("Marvin Gaming", 2))
        self.assertEqual([r["title"] for r in slog.recent(30, "tiktok", now=NOW)], ["Clip"])
        self.assertEqual(len(slog.recent(90, now=NOW)), 4)
        self.assertEqual([r["title"] for r in slog.recent(30, source_id="s1", now=NOW)], ["Neues Video", "Zweites"])

    def test_record_never_raises(self):
        from unittest.mock import patch
        with patch.object(pulse_db, "connect", side_effect=RuntimeError("x")):
            self.assertFalse(slog.record(SOURCES["s1"], {"title": "x"}))
        self.assertTrue(slog.record({}, {}))                      # leere Eingaben sind erlaubt und werden begrenzt gespeichert

    def test_table_is_part_of_the_backup(self):
        self.assertIn("social_publications", pulse_db.TABLES)


class PageTests(WikiBase):
    def setUp(self):
        super().setUp()
        ws.app.state.bot = SimpleNamespace(get_cog=lambda name: SimpleNamespace(sources=dict(SOURCES)) if name == "SocialPromo" else None)
        self.addCleanup(lambda: setattr(ws.app.state, "bot", None))
        slog.record(SOURCES["s1"], {"title": "<b>Video</b>", "url": "https://youtu.be/abc"})
        slog.record(SOURCES["s2"], {"title": "Clip", "url": "javascript:alert(1)"})

    def get_page(self, query=""):
        with self.as_user(MANAGER):
            resp = self.client.get("/social" + query)
        self.assertEqual(resp.status_code, 200)
        return resp.text

    def test_page_lists_filters_and_publications(self):
        html = self.get_page()
        for needle in ("Marvin Gaming", "Tik Tanja", "4 von 4 Quellen", "Veröffentlichungen (30 Tage)", "2 Ankündigungen"):
            self.assertIn(needle, html)

    def test_filter_narrows_the_sources_but_not_the_totals(self):
        html = self.get_page("?platform=tiktok")
        self.assertIn("Tik Tanja", html)
        self.assertNotIn("Marvin Gaming</div>", html.split("Veröffentlichungen")[0].split("Überwachte Kanäle")[1])
        self.assertIn("1 von 4 Quellen", html)
        self.assertIn("Zurücksetzen", html)
        self.assertIn("Keine Quelle passt zu diesem Filter", self.get_page("?q=gibtesnicht"))

    def test_output_is_escaped_and_unsafe_links_are_not_rendered(self):
        html = self.get_page()
        self.assertNotIn("<b>Video</b>", html)
        self.assertNotIn("javascript:alert(1)", html)
        self.assertIn("https://youtu.be/abc", html)

    def test_team_members_without_the_right_are_refused(self):
        with self.as_user(TEAM):
            self.assertEqual(self.client.get("/social").status_code, 403)


if __name__ == "__main__":
    unittest.main()
