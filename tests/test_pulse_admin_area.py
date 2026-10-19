"""Team vs. Verwaltung: access control and persistence of the Discord-Auftritt page.

Runs the real FastAPI routes with a fake login (no Discord connection, temp database).
"""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_db
import webserver as ws
from pulse_admin import admin_sections, can_open_admin
from pulse_embed_design import invalidate_cache
from pulse_welcome import SETTING_KEY

CHANNEL = SimpleNamespace(id=111111111111111111, name="willkommen", position=1)
GUILD = SimpleNamespace(id=1, name="© BOCHUM RP | V1", members=[], roles=[], channels=[CHANNEL], text_channels=[CHANNEL],
                        member_count=321, owner_id=0, icon=None,
                        get_member=lambda _: None, get_role=lambda _: None,
                        get_channel=lambda cid: CHANNEL if cid == CHANNEL.id else None)
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64


def fake_auth_for(perms):
    def fake_auth(request, session, perm="can_view_dashboard", admin=False):
        if admin and not perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="Dieser Bereich ist nur für Administratoren.")
        if perm and not perms.get(perm):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        return SimpleNamespace(user={"id": "123", "global_name": "Tester"}, guild=GUILD, guild_name=GUILD.name,
                               perms=perms, config={"team_role_ids": []}, member=None, msg="", msg_ok=True)
    return fake_auth


ADMIN = dict({k: True for k in ws.PERM_KEYS}, is_admin=True)
TEAMLER = dict({k: False for k in ws.PERM_KEYS}, can_view_dashboard=True, can_manage_tickets=True, is_admin=False)
ANNOUNCER = dict(TEAMLER, can_manage_announcements=True)


class AdminSectionTests(unittest.TestCase):
    def test_sections_follow_permissions(self):
        self.assertFalse(can_open_admin(TEAMLER))
        self.assertEqual([s[0] for s in admin_sections(ANNOUNCER)], ["announcements", "social"])
        keys = [s[0] for s in admin_sections(ADMIN)]
        for key in ("admin-discord", "ticket-panels", "settings", "backups", "system"):
            self.assertIn(key, keys)


class AdminRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
            patch.object(ws, "BASE_DIR", Path(self.tmp.name)),
            patch.object(ws, "log_audit", lambda *a, **k: None),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        invalidate_cache()
        self.client = TestClient(ws.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        invalidate_cache()
        self.tmp.cleanup()

    def as_user(self, perms):
        return patch.object(ws, "auth", fake_auth_for(perms))

    def test_teamler_cannot_reach_administration(self):
        with self.as_user(TEAMLER):
            for method, url in (("get", "/admin"), ("get", "/admin/discord"), ("post", "/admin/discord/welcome"),
                                ("post", "/admin/discord/branding"), ("get", "/admin/discord/banner/welcome_banner_0a1b2c3d.png")):
                response = getattr(self.client, method)(url, follow_redirects=False)
                self.assertEqual(response.status_code, 403, url)
            response = self.client.post("/admin/discord/test", data={"kind": "welcome"}, follow_redirects=False)
            self.assertEqual(response.status_code, 403)
            self.assertEqual(pulse_db.get_setting(SETTING_KEY, None), None)

    def test_team_sidebar_has_no_admin_switch_for_teamler(self):
        with self.as_user(TEAMLER):
            html = self.client.get("/me").text
        self.assertNotIn('href="/admin"', html)

    def test_announcer_sees_only_permitted_admin_sections(self):
        with self.as_user(ANNOUNCER):
            html = self.client.get("/admin").text
        self.assertIn("Ankündigungen", html)
        self.assertNotIn("Discord-Auftritt", html)
        self.assertNotIn("/backups", html.split("pulse-shell", 1)[-1])

    def test_admin_page_renders_with_switch_and_preview(self):
        with self.as_user(ADMIN):
            html = self.client.get("/admin/discord").text
        self.assertIn("Willkommen", html)
        self.assertIn("dc-embed", html)
        self.assertIn("pulse-area-switch", html)
        self.assertIn("#willkommen", html)

    def form(self, **overrides):
        data = {"welcome_enabled": "1", "welcome_channel_id": str(CHANNEL.id), "welcome_title": "Hallo {display_name}",
                "welcome_message": "Willkommen {user} auf {server_name}", "welcome_mention": "1", "welcome_show_avatar": "1",
                "welcome_banner_url": "", "leave_enabled": "", "leave_channel_id": "", "leave_title": "Tschüss {display_name}",
                "leave_message": "Bis bald", "leave_show_avatar": "1", "leave_banner_url": ""}
        data.update(overrides)
        return data

    def test_save_persists_and_upload_is_stored(self):
        with self.as_user(ADMIN):
            response = self.client.post("/admin/discord/welcome", data=self.form(),
                                        files={"welcome_banner_upload": ("mein banner.png", PNG, "image/png")},
                                        follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        saved = pulse_db.get_setting(SETTING_KEY, {})
        self.assertTrue(saved["welcome"]["enabled"])
        self.assertEqual(saved["welcome"]["title"], "Hallo {display_name}")
        self.assertRegex(saved["welcome"]["banner_file"], r"^welcome_banner_[0-9a-f]{8}\.png$")
        stored = Path(self.tmp.name) / "uploads" / "welcome_banners" / saved["welcome"]["banner_file"]
        self.assertTrue(stored.is_file())
        with self.as_user(ADMIN):
            self.assertEqual(self.client.get(f"/admin/discord/banner/{saved['welcome']['banner_file']}").status_code, 200)
            # remove the banner again
            self.client.post("/admin/discord/welcome", data=self.form(welcome_remove_banner="1"), follow_redirects=False)
        self.assertEqual(pulse_db.get_setting(SETTING_KEY, {})["welcome"]["banner_file"], "")
        self.assertFalse(stored.exists())

    def test_invalid_input_is_rejected_and_kept_in_form(self):
        with self.as_user(ADMIN):
            response = self.client.post("/admin/discord/welcome", data=self.form(welcome_message="Hi {vorname}"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("Nicht gespeichert", response.text)
        self.assertIn("Hi {vorname}", response.text)
        self.assertIsNone(pulse_db.get_setting(SETTING_KEY, None))
        with self.as_user(ADMIN):
            response = self.client.post("/admin/discord/welcome", data=self.form(),
                                        files={"welcome_banner_upload": ("x.png", b"<?php echo 1; ?>", "image/png")})
        self.assertIn("nur PNG", response.text)
        self.assertFalse((Path(self.tmp.name) / "uploads" / "welcome_banners").exists()
                         and any((Path(self.tmp.name) / "uploads" / "welcome_banners").iterdir()))
        with self.as_user(ADMIN):
            response = self.client.post("/admin/discord/welcome", data=self.form(welcome_banner_url="http://unsicher.example/a.png"))
        self.assertIn("https://", response.text)

    def test_banner_route_blocks_traversal(self):
        with self.as_user(ADMIN):
            self.assertEqual(self.client.get("/admin/discord/banner/..%2Fpulse.db").status_code, 404)
            self.assertEqual(self.client.get("/admin/discord/banner/welcome_banner_0a1b2c3d.png").status_code, 404)

    def test_branding_save_and_reset(self):
        with self.as_user(ADMIN):
            self.client.post("/admin/discord/branding", data={"brand_name": "Pulse", "footer_text": "BORP",
                                                              "color_success": "#00ff00"}, follow_redirects=False)
            self.assertEqual(pulse_db.get_setting("discord_branding")["colors"]["success"], "#00FF00")
            bad = self.client.post("/admin/discord/branding", data={"color_success": "red"}, follow_redirects=False)
            self.assertIn("t=err", bad.headers["location"])
            self.client.post("/admin/discord/branding", data={"reset": "1"}, follow_redirects=False)
            self.assertEqual(pulse_db.get_setting("discord_branding")["footer_text"], "© BOCHUM RP | V1 · Pulse TeamOS")

    def test_verification_config_is_admin_only(self):
        with self.as_user(TEAMLER):
            for url in ("/admin/discord/verification", "/admin/discord/verification/publish"):
                self.assertEqual(self.client.post(url, data={"enabled": "1", "channel_id": str(CHANNEL.id)},
                                                  follow_redirects=False).status_code, 403)
        self.assertIsNone(pulse_db.get_setting("verification_config", None))

    def test_verification_save_validates_and_keeps_publication_state(self):
        pulse_db.set_setting("verification_config", {"message_id": "900000000000000001", "published_channel_id": str(CHANNEL.id)})
        with self.as_user(ADMIN):
            bad = self.client.post("/admin/discord/verification", data={"enabled": "1", "channel_id": ""})
            self.assertIn("Verifikationskanal", bad.text)
            foreign = self.client.post("/admin/discord/verification", data={"channel_id": "999999999999999999"})
            self.assertIn("gehört nicht zu diesem Server", foreign.text)
            ok = self.client.post("/admin/discord/verification", data={"enabled": "1", "channel_id": str(CHANNEL.id),
                                                                       "title": "Bitte verifizieren", "button_label": "Los"},
                                  follow_redirects=False)
        self.assertEqual(ok.status_code, 303)
        saved = pulse_db.get_setting("verification_config")
        self.assertTrue(saved["enabled"])
        self.assertEqual(saved["button_label"], "Los")
        self.assertEqual(saved["message_id"], "900000000000000001")

    def test_verification_publish_without_running_bot_fails_cleanly(self):
        with self.as_user(ADMIN):
            response = self.client.post("/admin/discord/verification/publish", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn("t=err", response.headers["location"])

    def test_test_message_requires_saved_channel(self):
        with self.as_user(ADMIN):
            response = self.client.post("/admin/discord/test", data={"kind": "welcome"}, follow_redirects=False)
        self.assertIn("t=err", response.headers["location"])


if __name__ == "__main__":
    unittest.main()
