"""Audit #58/#62/#67/#68/#70: live status + feed, keyboard help, contextual help and the wider global search
(fake login, temp databases, no Discord)."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import pulse_help
import pulse_keys
import pulse_live as live
import webserver as ws

LEAD = {"can_view_dashboard": True, "can_promote": True}
TEAM = {"can_view_dashboard": True}
WARNER = {"can_view_dashboard": True, "can_warn": True}


def entry(i, action, actor="Ann", details="GEHEIMER-DETAIL"):
    return {"id": f"audit_{i:03d}", "actor": actor, "actor_id": "1", "action": action, "details": details, "timestamp": f"15.11.2026 10:{i:02d}:00"}


LOG = [entry(1, "Ticket geschlossen"), entry(2, "Verwarnung erteilt"), entry(3, "Notiz hinzugefügt"), entry(4, "Bewerbung angenommen"), entry(5, "Rolle geändert")]


class FeedLogicCase(unittest.TestCase):
    def actions(self, perms, **kw):
        return [i["action"] for i in live.feed_items(LOG, perms, **kw)]

    def test_newest_first_and_no_detail_text(self):
        items = live.feed_items(LOG, LEAD)
        self.assertEqual(items[0]["id"], "audit_005")
        for item in items:
            self.assertEqual(set(item), {"id", "at", "actor", "action"})
            self.assertNotIn("GEHEIMER-DETAIL", str(item))

    def test_sensitive_actions_follow_permissions(self):
        self.assertEqual(self.actions(TEAM), ["Rolle geändert", "Ticket geschlossen"])
        self.assertIn("Verwarnung erteilt", self.actions(WARNER))
        self.assertNotIn("Bewerbung angenommen", self.actions(WARNER))
        self.assertEqual(len(self.actions(LEAD)), 4)  # leadership: no warnings without can_warn
        self.assertEqual(len(self.actions(dict(LEAD, is_admin=True))), 5)

    def test_after_limit_and_garbage(self):
        self.assertEqual([i["id"] for i in live.feed_items(LOG, dict(LEAD, is_admin=True), after="audit_003")], ["audit_005", "audit_004"])
        self.assertEqual(len(live.feed_items(LOG, dict(LEAD, is_admin=True), limit=2)), 2)
        self.assertEqual(live.feed_items([None, "x", {}, {"action": "ohne id"}], LEAD), [])
        self.assertEqual(live.feed_items(None, LEAD), [])


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")), patch.object(ws, "AUDIT_FILE", str(root / "audit.json")),
                        patch.object(ws, "SHIFTS_FILE", str(root / "shifts.json"))]
        for p in self.patches:
            p.start()
        pulse_db._SCHEMA_READY.discard(str(root / "pulse.db"))
        pulse_db.init_db()
        ws.save_json(ws.AUDIT_FILE, LOG)
        ws.save_json(ws.SHIFTS_FILE, {"active_shifts": {"1": {"status": "online"}, "2": {"status": "break"}, "3": {"status": "ended"}}, "history": []})
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    @staticmethod
    def as_user(perms, uid="1"):
        def auth(request, session, perm="can_view_dashboard", admin=False):
            if perm and not perms.get(perm):
                raise HTTPException(status_code=403, detail="nein")
            return SimpleNamespace(user={"id": uid, "global_name": "T"}, guild=None, guild_name="T", perms=perms, config={}, member=None, msg="", msg_ok=True)
        return patch.object(ws, "auth", auth)


class LiveRouteCase(Base):
    def test_status_counts_and_no_store(self):
        with self.as_user(TEAM):
            resp = self.client.get("/api/live/status")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["cache-control"], "no-store")
        data = resp.json()
        self.assertEqual((data["on_duty"], data["unread"], data["poll_seconds"]), (2, 0, live.POLL_SECONDS))
        self.assertIn("bot_ready", data)

    def test_status_requires_dashboard_access(self):
        with self.as_user({}):
            self.assertEqual(self.client.get("/api/live/status").status_code, 403)

    def test_feed_is_leadership_only_and_returns_only_new_entries(self):
        with self.as_user(TEAM):
            self.assertEqual(self.client.get("/api/live/feed").status_code, 403)
        with self.as_user(dict(LEAD, is_admin=True)):
            first = self.client.get("/api/live/feed").json()["items"]
            self.assertEqual(first[0]["id"], "audit_005")
            self.assertEqual(self.client.get("/api/live/feed?after=audit_005").json()["items"], [])
            ws.log_audit("Eve", "9", "Neue Aktion", "x")
            newer = self.client.get("/api/live/feed?after=audit_005").json()["items"]
        self.assertEqual([i["action"] for i in newer], ["Neue Aktion"])
        self.assertNotIn("GEHEIMER", str(first))

    def test_widget_markup_has_feed_only_for_leadership(self):
        self.assertIn('<ul id="liveFeed"', live.widget_html(ws.esc, True))
        self.assertNotIn('<ul id="liveFeed"', live.widget_html(ws.esc, False))
        self.assertIn("document.hidden", live.widget_html(ws.esc, False))
        html = live.widget_html(ws.esc, False)  # #117/#70: Push per SSE, Polling bleibt Rückfall, Stream wird bei verstecktem Tab geschlossen
        for needle in ("/api/events/stream", "window.EventSource", "setInterval", "closeStream"):
            self.assertIn(needle, html)


class KeysAndHelpCase(unittest.TestCase):
    def test_dialog_is_hidden_by_default_and_labelled(self):
        html = pulse_keys.dialog_html()
        self.assertIn('role="dialog"', html)
        self.assertIn("display:none", html)
        self.assertNotIn("display:flex;align", html)
        self.assertIn("aria-labelledby", html)
        for _k, url, _label in pulse_keys.NAV:
            self.assertIn(url, html)
        self.assertIn("pulseKeysOpen", pulse_keys.button_html())

    def test_shell_contains_keyboard_help_on_pro_pages(self):
        import pulse_pro
        ctx = SimpleNamespace(perms={}, user={"id": "1"})
        with patch.object(ws, "render_page", lambda title, c, page, body, extra="": body):
            html = pulse_pro.render_pro_page(ws, "T", ctx, "me", "<p>x</p>")
        self.assertIn("pulseKeysDialog", html)
        self.assertIn("pulseKeysOpen", html)
        self.assertIn("pulseCommandModal", html)

    def test_help_tips_escape_and_unknown_is_empty(self):
        self.assertEqual(pulse_help.tip("gibt-es-nicht"), "")
        self.assertIn("<details", pulse_help.tip("readiness"))
        with patch.dict(pulse_help.TOPICS, {"x": "<script>alert(1)</script>"}):
            self.assertNotIn("<script>", pulse_help.tip("x"))
        for key in pulse_help.TOPICS:
            self.assertTrue(pulse_help.tip(key))


class SearchCase(unittest.TestCase):
    def setUp(self):
        from cogs import pulse_suite
        self.suite = pulse_suite
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(base / "pulse.db")), patch.object(ws, "AUDIT_FILE", str(base / "audit.json")),
                        patch.object(ws, "DATA_FILE", str(base / "team.json")), patch.object(ws, "APPS_FILE", str(base / "apps.json")),
                        patch.object(pulse_suite, "DB_PATH", str(base / "suite.db"))]
        for p in self.patches:
            p.start()
        pulse_db._SCHEMA_READY.discard(str(base / "pulse.db"))
        pulse_db.init_db()
        pulse_suite.init_db()
        page = pulse_db.save_wiki("Ticket-Regeln", "Support", "Alte Regel: Zebrastreifen-Pflicht", "1", "Ann")
        pulse_db.save_wiki("Ticket-Regeln", "Support", "Neue Regel: Ampelpflicht", "1", "Ann", page_id=page["id"] if isinstance(page, dict) else page)
        ws.save_json(ws.AUDIT_FILE, [{"id": "a1", "actor": "Eve", "actor_id": "9", "action": "Quokka-Aktion", "details": "d", "timestamp": "x"}])

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def search(self, term, perms):
        ctx = SimpleNamespace(guild=SimpleNamespace(members=[], get_member=lambda i: None), perms=perms, user={"id": "7"})
        return self.suite.search_all(term, ctx)

    def test_wiki_current_text_is_found_for_everybody(self):
        hits = [r for r in self.search("ampelpflicht", TEAM) if r["type"] == "Wiki"]
        self.assertEqual(len(hits), 1)
        self.assertTrue(hits[0]["url"].startswith("/wiki/"))

    def test_old_versions_and_legacy_audit_only_for_leadership(self):
        self.assertEqual([r for r in self.search("zebrastreifen", TEAM) if r["type"] == "Wiki-Version"], [])
        self.assertEqual(len([r for r in self.search("zebrastreifen", LEAD) if r["type"] == "Wiki-Version"]), 1)
        self.assertEqual([r for r in self.search("quokka", TEAM) if r["type"] == "Audit (alt)"], [])
        self.assertEqual(len([r for r in self.search("quokka", LEAD) if r["type"] == "Audit (alt)"]), 1)

    def test_a_failing_source_does_not_break_the_search(self):
        with patch.object(pulse_db, "wiki_pages", side_effect=RuntimeError("db")):
            self.assertIsInstance(self.search("quokka", LEAD), list)


if __name__ == "__main__":
    unittest.main()
