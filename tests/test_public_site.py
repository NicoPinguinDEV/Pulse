"""Public website: pages, separation from the dashboard, legal state, consent and exposure."""
import base64
import inspect
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_public_consent as public_team_consent
import pulse_public_legal as legal
import pulse_public_site as site
import pulse_security
import pulse_retention
import webserver as ws

LEGAL_ENV = {
    "PULSE_LEGAL_NAME": "Test Person", "PULSE_LEGAL_ADDRESS": "Teststraße 1|12345 Teststadt",
    "PULSE_LEGAL_EMAIL": "kontakt@example.invalid", "PULSE_LEGAL_HOSTER": "Test Hosting GmbH",
    "PULSE_LEGAL_REVIEWED": "1", "PULSE_TERMS_APPROVED": "1", "PULSE_TERMS_EFFECTIVE_DATE": "2026-11-06",
    "PULSE_LEGAL_DDG_ASSESSMENT": "not_applicable",
    "PULSE_LEGAL_MSTV_18_2_ASSESSMENT": "not_applicable",
}
PAGES = ("/", "/regelwerk", "/faq", "/fraktionen", "/news", "/team-vorstellung", "/nutzungsbedingungen",
         "/impressum", "/datenschutz", "/cookies", "/sicherheit")


def clean_env():
    return {key: value for key, value in os.environ.items() if not key.startswith(("PULSE_LEGAL_", "PULSE_PUBLIC_", "PULSE_TERMS_"))}


class Base(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, clean_env(), clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        db = patch("pulse_db.DB_PATH", str(Path(tmp.name) / "pulse.db"))
        db.start()
        self.addCleanup(db.stop)
        site._cache.clear()
        self.addCleanup(site._cache.clear)
        ws._rate_limits.clear() if hasattr(ws, "_rate_limits") else None
        self.client = TestClient(ws.app, raise_server_exceptions=False, follow_redirects=False)


class Pages(Base):
    def test_dashboard_head_never_uses_tailwind_play_cdn(self):
        self.assertNotIn("cdn.tailwindcss.com", ws.HEAD_SCRIPT)
        self.assertIn("/static/pulse.css", ws.HEAD_SCRIPT)

    def test_every_page_renders_with_strict_policy(self):
        for path in PAGES:
            with self.subTest(path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertIn("script-src 'self'", response.headers["content-security-policy"])
                self.assertNotIn("unsafe-inline", response.headers["content-security-policy"])
                self.assertIn("Anmelden", response.text)
                self.assertNotIn("set-cookie", response.headers)

    def test_no_external_resource_and_no_inline_code(self):
        for path in PAGES:
            html = self.client.get(path).text
            external_assets = re.findall(r'<(?:script|link|img)[^>]+(?:src|href)="(https?://[^"]+)', html)
            self.assertEqual(external_assets, [], path)
            self.assertNotIn("<style", html)
            self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)(?![^>]*application/json)", html), path)
            self.assertIsNone(re.search(r"\sstyle=\"", html), path)
            self.assertIsNone(re.search(r"\son[a-z]+=\"", html), path)

    def test_static_files(self):
        self.assertEqual(self.client.get("/static/public.css").status_code, 200)
        self.assertIn("javascript", self.client.get("/static/public.js").headers["content-type"])
        self.assertEqual(self.client.get("/static/..%2f..%2fwebserver.py").status_code, 404)
        self.assertEqual(self.client.get("/static/tailwind.input.css").status_code, 404)
        self.assertNotIn(self.client.get("/static/pulse.css").status_code, (405,))  # dashboard CSS keeps its own route

    def test_rules_show_rulebooks_but_no_assistant_instructions(self):
        html = self.client.get("/regelwerk").text
        self.assertIn("§12 Safezones", html)
        self.assertIn("Discord", html)
        self.assertNotIn("Verbindliche Antwortregeln", html)
        self.assertNotIn("Erfinde keine", html)

    def test_no_invented_invite(self):
        self.assertNotIn("discord.gg", self.client.get("/").text)
        with patch.dict(os.environ, {"PULSE_PUBLIC_DISCORD_URL": "https://evil.example/x"}):
            self.assertNotIn("evil.example", self.client.get("/").text)
        with patch.dict(os.environ, {"PULSE_PUBLIC_DISCORD_URL": "https://discord.gg/abc123"}):
            self.assertIn('href="https://discord.gg/abc123"', self.client.get("/").text)

    def test_member_count_is_real_or_absent(self):
        self.assertNotIn("Mitglieder im Discord", self.client.get("/").text)
        guild = SimpleNamespace(member_count=1234)
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(bot=SimpleNamespace(get_guild=lambda _: guild))))
        self.assertIn("1.234", site.home_html(request))

    def test_news_escape_and_order(self):
        site.add_news("<b>Erste</b>", "Text <script>x</script>", "Admin")
        site.add_news("Zweite", "Absatz", "Admin")
        html = self.client.get("/news").text
        self.assertNotIn("<script>x", html)
        self.assertIn("&lt;b&gt;Erste", html)
        self.assertLess(html.index("Zweite"), html.index("Erste"))

    def test_news_needs_content(self):
        with self.assertRaises(ValueError):
            site.add_news(" ", "Text", "Admin")

    def test_factions_show_public_fields_only(self):
        rows = [{"name": "Polizei", "category_name": "Staatlich", "description": "Ordnung", "warnings": 3, "leader_id": "4711"}]
        with patch("pulse_factions.list_factions", return_value=rows) as load:
            html = self.client.get("/fraktionen").text
        load.assert_called_once_with(status="official")
        self.assertIn("Polizei", html)
        self.assertNotIn("4711", html)

    def test_rate_limit(self):
        with patch.object(ws, "rate_limited", return_value=True):
            self.assertEqual(self.client.get("/faq").status_code, 429)

    def test_news_articles_have_individual_pages_and_categories(self):
        news_id = site.add_news("Eröffnung", "Wir starten bald.", "Admin", "Ankündigung")
        listing = self.client.get("/news").text
        detail = self.client.get(f"/news/{news_id}")
        self.assertEqual(detail.status_code, 200)
        self.assertIn("Ankündigung", listing)
        self.assertIn("Eröffnung", detail.text)
        self.assertIn("Wir starten bald.", detail.text)
        self.assertEqual(self.client.get("/news/999999").status_code, 404)

    def test_homepage_uses_updated_visual_hierarchy(self):
        html = self.client.get("/").text
        for marker in (
            'class="hero-kicker"',
            'class="hero-lead"',
            'class="hero-meta"',
            'class="hero-scroll"',
            'class="section-kicker"',
            'BOCHUM <span>RP</span>',
        ):
            self.assertIn(marker, html)

    def test_public_visual_refresh_has_accessible_motion_fallbacks(self):
        css = (Path(site.ROOT) / "static" / "public.css").read_text(encoding="utf-8")
        js = (Path(site.ROOT) / "static" / "public.js").read_text(encoding="utf-8")
        self.assertIn(".reveal-ready.is-revealed", css)
        self.assertIn("prefers-reduced-motion: reduce", css)
        self.assertIn("function startScrollReveals()", js)
        self.assertIn('window.matchMedia("(prefers-reduced-motion: reduce)")', js)
        self.assertIn('body.style.setProperty("--cursor-x"', js)

    def test_public_team_page_uses_current_configured_discord_roles(self):
        import cogs.teamliste as teamliste
        member = SimpleNamespace(
            id=42, display_name="Nico", status="offline",
            display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/avatars/42/hash.png"),
        )
        role = SimpleNamespace(id=101, name="Founder", position=90, members=[member])
        public_team_consent.grant(42, [101])
        guild = SimpleNamespace(get_role=lambda role_id: role if role_id == 101 else None)
        bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
        ws.app.state.bot = bot
        self.addCleanup(lambda: setattr(ws.app.state, "bot", None))
        with patch.object(teamliste, "get_roles_by_category",
                          side_effect=lambda category: [101] if category == "fuehrungsebene" else []):
            html = self.client.get("/team-vorstellung").text
        self.assertIn("Die Köpfe hinter BOCHUM RP", html)
        self.assertIn("1 Mitglied", html)
        self.assertIn("Founder", html)
        self.assertIn("Nico", html)
        self.assertNotIn("cdn.discordapp.com/avatars/42/hash.png", html)
        self.assertIn('class="team-initials"', html)
        self.assertNotIn("offline", html)
        self.assertNotIn("Abgemeldet", html)

    def test_team_consent_disclosure_matches_public_profile_output(self):
        disclosure = (Path(site.ROOT) / "cogs" / "teamliste.py").read_text(encoding="utf-8")
        self.assertEqual(public_team_consent.CONSENT_VERSION, 2)
        self.assertIn("Profilbilder werden nicht eingebunden", disclosure)
        self.assertIn("lädt keine Bildressourcen von Discord-CDN-Domains", disclosure)
        self.assertNotIn("dein Discord-Profilbild anzeigt", disclosure)
        self.assertNotIn("Profilbilder werden direkt von Discord-CDN-Domains geladen", disclosure)

    def test_public_site_pointer_effect_is_progressive_and_respects_reduced_motion(self):
        public_css = (Path(site.ROOT) / "static" / "public.css").read_text(encoding="utf-8")
        public_js = (Path(site.ROOT) / "static" / "public.js").read_text(encoding="utf-8")
        self.assertIn("(pointer: fine)", public_css)
        self.assertIn("prefers-reduced-motion: no-preference", public_css)
        self.assertIn("prefers-reduced-motion: reduce", public_css)
        self.assertIn("function startPointerEffects()", public_js)
        self.assertIn("startPointerEffects();", public_js)

    def test_public_team_page_requires_explicit_consent_and_hides_after_withdrawal(self):
        import cogs.teamliste as teamliste
        member = SimpleNamespace(
            id=303, display_name="Testmitglied", status="offline",
            display_avatar=SimpleNamespace(url="https://cdn.discordapp.com/avatars/303/test.png"),
        )
        role = SimpleNamespace(id=3030, name="Support", position=10, members=[member])
        guild = SimpleNamespace(get_role=lambda role_id: role if role_id == 3030 else None)
        ws.app.state.bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
        self.addCleanup(lambda: setattr(ws.app.state, "bot", None))
        with patch.object(teamliste, "get_roles_by_category",
                          side_effect=lambda category: [3030] if category == "lowteam" else []):
            hidden = self.client.get("/team-vorstellung").text
            self.assertNotIn("Testmitglied", hidden)
            self.assertIn("freiwilligen, ausdrücklich gespeicherten Freigabe", hidden)
            public_team_consent.grant(303, [3030])
            visible = self.client.get("/team-vorstellung").text
            self.assertIn("Testmitglied", visible)
            self.assertEqual(self.client.get("/team-vorstellung").headers.get("cache-control"), "no-store")
            self.assertIn("Support", visible)
            self.assertNotIn("offline", visible)
            self.assertTrue(public_team_consent.withdraw(303))
            withdrawn = self.client.get("/team-vorstellung").text
            self.assertNotIn("Testmitglied", withdrawn)

    def test_public_team_page_requires_reconsent_when_configured_role_scope_changes(self):
        import cogs.teamliste as teamliste
        member = SimpleNamespace(
            id=404, display_name="Scope-Test",
            display_avatar=SimpleNamespace(url=""),
        )
        role_a = SimpleNamespace(id=4040, name="Support", position=10, members=[member])
        role_b = SimpleNamespace(id=4041, name="Leitung", position=20, members=[member])
        guild = SimpleNamespace(get_role=lambda role_id: {4040: role_a, 4041: role_b}.get(role_id))
        ws.app.state.bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
        self.addCleanup(lambda: setattr(ws.app.state, "bot", None))
        public_team_consent.grant(404, [4040])
        with patch.object(teamliste, "get_roles_by_category",
                          side_effect=lambda category: [4040, 4041] if category == "fuehrungsebene" else []):
            html = self.client.get("/team-vorstellung").text
        self.assertNotIn("Scope-Test", html)

    def test_role_update_revokes_consent_and_still_schedules_teamlist_refresh(self):
        import cogs.teamliste as teamliste
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import patch

        cog = teamliste.TeamlisteCog.__new__(teamliste.TeamlisteCog)
        cog._update_debounce_task = None
        cog._debounced_update_teamlist = lambda _guild: asyncio.sleep(0)
        public_team_consent.grant(606, [6060])
        before = SimpleNamespace(
            id=606,
            roles=[SimpleNamespace(id=6060)],
            display_name="Before",
            guild=SimpleNamespace(),
        )
        after = SimpleNamespace(
            id=606,
            roles=[SimpleNamespace(id=6060), SimpleNamespace(id=6061)],
            display_name="After",
            guild=before.guild,
        )
        cog.public_team_role_ids = lambda member: sorted(role.id for role in member.roles)
        dummy_task = SimpleNamespace(done=lambda: True)

        def consume_coroutine(coro, **_kwargs):
            coro.close()
            return dummy_task

        with patch.object(teamliste, "spawn_task", side_effect=consume_coroutine) as spawn:
            asyncio.run(cog.on_member_update(before, after))

        self.assertNotIn("606", public_team_consent.active_consents())
        spawn.assert_called_once()

    def test_public_team_consent_records_version_and_withdrawal(self):
        accepted = public_team_consent.grant(505, [5050, 5051, 5050])
        self.assertEqual(accepted["role_ids"], [5050, 5051])
        current = public_team_consent.active_consents()["505"]
        self.assertEqual(current["role_ids"], [5050, 5051])
        self.assertTrue(public_team_consent.withdraw(505))
        self.assertNotIn("505", public_team_consent.active_consents())
        self.assertFalse(public_team_consent.withdraw(505))

    def test_public_team_page_removes_members_when_they_leave_configured_role(self):
        import cogs.teamliste as teamliste
        member = SimpleNamespace(id=99, display_name="Mara", status="online",
                                 display_avatar=SimpleNamespace(url=""))
        role = SimpleNamespace(id=202, name="Supporter", position=10, members=[member])
        public_team_consent.grant(99, [202])
        guild = SimpleNamespace(get_role=lambda role_id: role if role_id == 202 else None)
        ws.app.state.bot = SimpleNamespace(get_guild=lambda _guild_id: guild)
        self.addCleanup(lambda: setattr(ws.app.state, "bot", None))
        with patch.object(teamliste, "get_roles_by_category",
                          side_effect=lambda category: [202] if category == "lowteam" else []):
            self.assertIn("Mara", self.client.get("/team-vorstellung").text)
            role.members = []
            self.assertNotIn("Mara", self.client.get("/team-vorstellung").text)


class Separation(Base):
    def test_team_route_is_not_public(self):
        response = self.client.get("/team")
        self.assertIn(response.status_code, (302, 303, 401, 403, 503))
        self.assertNotIn("Bochum wartet", response.text)

    def test_dashboard_stays_protected(self):
        self.assertIn(self.client.get("/dashboard").status_code, (302, 303, 401, 503))

    def test_public_homepage_stays_public_for_signed_in_member(self):
        self.client.cookies.set("user_session", "valid")
        with patch.object(ws, "dashboard_session", return_value={"id": "1"}), \
                patch.object(ws, "start_page_for", return_value="/tickets"):
            response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("BOCHUM", response.text)
        self.assertNotIn("Team Management Panel", response.text)

    def test_team_login_takes_signed_in_member_to_start_page(self):
        self.client.cookies.set("user_session", "valid")
        with patch.object(ws, "dashboard_session", return_value={"id": "1"}), \
                patch.object(ws, "start_page_for", return_value="/tickets"):
            response = self.client.get("/team-login")
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/tickets")

    def test_login_card_moved_with_privacy_notice(self):
        html = self.client.get("/team-login").text
        self.assertIn("Mit Discord anmelden", html)
        self.assertIn("IP-Adresse", html)
        self.assertIn('href="/login"', html)

    def test_expired_session_cookie_is_cleared_on_home(self):
        self.client.cookies.set("user_session", "alt")
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("user_session", response.headers.get("set-cookie", ""))

    def test_apply_stays_available(self):
        self.assertEqual(self.client.get("/apply").status_code, 200)

    def test_website_admin_requires_login(self):
        self.assertIn(self.client.get("/admin/website").status_code, (302, 303, 401, 403, 503))
        response = self.client.post("/admin/website/news", data={"title": "x", "body": "y"})
        self.assertIn(response.status_code, (302, 303, 401, 403, 503))
        self.assertIn(self.client.get("/admin/website/team").status_code, (302, 303, 401, 403, 503))
        self.assertEqual(site.list_news(), [])


class WebsiteAdmin(Base):
    def test_public_website_admin_is_in_administration_navigation(self):
        import pulse_admin
        self.assertIn("website", pulse_admin.ADMIN_PAGE_KEYS)
        self.assertTrue(any(
            item[0] == "website" and item[1] == "/admin/website"
            for item in pulse_admin.admin_sections({"is_admin": True})
        ))

    def login(self, is_admin):
        guild = SimpleNamespace(id=1, name="g", chunked=True, get_member=lambda _: None, get_role=lambda _: None,
                                roles=[], member_count=1)
        bot = SimpleNamespace(get_guild=lambda _: guild, is_ready=lambda: True, latency=0.1)
        ws.app.state.bot = bot
        self.addCleanup(lambda: setattr(ws.app.state, "bot", None))
        for target, value in (
            ("get_current_user", lambda _s: {"id": "7", "global_name": "Tess", "username": "tess"}),
            ("compute_perms", lambda _g, _u, _c: ({"is_admin": is_admin, "can_view_dashboard": True}, SimpleNamespace(id=7))),
            ("log_audit", lambda *_a, **_k: None),
        ):
            active = patch.object(ws, target, value)
            active.start()
            self.addCleanup(active.stop)

    def test_team_member_without_admin_cannot_publish(self):
        self.login(False)
        response = self.client.post("/admin/website/news", data={"title": "x", "body": "y"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(site.list_news(), [])

    def test_admin_publishes_and_removes(self):
        self.login(True)
        response = self.client.post("/admin/website/news", data={"title": "Serverneustart", "body": "Text"})
        self.assertEqual(response.status_code, 303)
        self.assertIn("Serverneustart", self.client.get("/news").text)
        news_id = site.list_news()[0]["id"]
        self.assertEqual(self.client.post(f"/admin/website/news/{news_id}/delete").status_code, 303)
        self.assertNotIn("Serverneustart", self.client.get("/news").text)

    def test_legacy_manual_profiles_do_not_override_the_live_discord_roster(self):
        import pulse_public_content as content
        self.login(True)
        profile_id = content.create_profile("Mara", "Support", "Legacy-Datensatz")
        approved = self.client.post(
            f"/admin/website/team/{profile_id}/publish",
            data={"published": "1", "consent_confirmed": "1"},
        )
        self.assertEqual(approved.status_code, 303)
        with patch("cogs.teamliste.get_roles_by_category", return_value=[]):
            self.assertNotIn("Mara", self.client.get("/team-vorstellung").text)
        self.assertTrue(content.list_profiles(published_only=True))

    def test_admin_can_upload_a_local_png_portrait_and_withdraw_it(self):
        import pulse_public_content as content
        self.login(True)
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/XioAAAAASUVORK5CYII="
        )
        created = self.client.post(
            "/admin/website/team",
            data={"display_name": "Nico", "role_label": "Leitung", "bio": "Öffentliches Profil"},
            files={"avatar": ("portrait.png", png, "image/png")},
        )
        self.assertEqual(created.status_code, 303)
        profile_id = content.list_profiles(published_only=False)[0]["id"]
        with patch("cogs.teamliste.get_roles_by_category", return_value=[]):
            self.assertNotIn("data:image/png", self.client.get("/team-vorstellung").text)
        approved = self.client.post(
            f"/admin/website/team/{profile_id}/publish",
            data={"published": "1", "consent_confirmed": "1"},
        )
        self.assertEqual(approved.status_code, 303)
        with patch("cogs.teamliste.get_roles_by_category", return_value=[]):
            public_html = self.client.get("/team-vorstellung").text
        self.assertNotIn('class="team-avatar"', public_html)
        self.assertNotIn("data:image/png;base64,", public_html)
        self.assertNotIn("Nico", public_html)
        withdrawn = self.client.post(
            f"/admin/website/team/{profile_id}/publish", data={"published": "0"}
        )
        self.assertEqual(withdrawn.status_code, 303)
        with patch("cogs.teamliste.get_roles_by_category", return_value=[]):
            self.assertNotIn("data:image/png", self.client.get("/team-vorstellung").text)

    def test_admin_rejects_non_png_team_portraits(self):
        self.login(True)
        response = self.client.post(
            "/admin/website/team",
            data={"display_name": "Nico", "role_label": "Leitung"},
            files={"avatar": ("portrait.jpg", b"not a png", "image/jpeg")},
        )
        self.assertEqual(response.status_code, 422)

    def test_admin_page_shows_missing_details(self):
        self.login(True)
        response = self.client.get("/admin/website")
        self.assertEqual(response.status_code, 200)
        self.assertIn("PULSE_LEGAL_NAME", response.text)


class Legal(Base):
    def test_privacy_page_is_not_cached_and_contains_default_hosting_provider(self):
        response = self.client.get("/datenschutz")
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        for term in ("Henrik Kramer e.K.", "Prepaid-Hoster.de", "Vionity-Kundenportal", "Offenbach am Main"):
            self.assertIn(term, response.text)

    def test_cookie_notice_matches_application_session_and_browser_draft_lifetimes(self):
        html = self.client.get("/cookies").text
        self.assertIn("3 Stunden", html)
        self.assertIn("12 Stunden", html)
        self.assertIn("30 Tagen ohne Änderung", html)
        self.assertEqual(pulse_retention.APPLICATION_DRAFT_TTL_DAYS, 30)

    def test_application_privacy_notice_explains_next_visit_cleanup_and_server_retention(self):
        html = self.client.get("/datenschutz").text
        self.assertIn("gilt er als abgelaufen und wird beim nächsten Öffnen des Formulars entfernt", html)
        self.assertIn("nicht automatisch gelöscht", html)
        self.assertIn("Roblox-Spielersuche", html)
        self.assertIn("Thumbnail", html)

    def test_application_draft_has_expiry_and_explicit_delete_control(self):
        self.assertIn("__TTL_MS__", ws.APPLY_DRAFT_SCRIPT)
        self.assertIn("Date.now()-savedAt>TTL_MS", ws.APPLY_DRAFT_SCRIPT)
        self.assertIn("localStorage.removeItem(KEY)", ws.APPLY_DRAFT_SCRIPT)
        self.assertIn("Gespeicherten Entwurf löschen", ws.APPLY_DRAFT_SCRIPT)
        self.assertIn("restoring=true", ws.APPLY_DRAFT_SCRIPT)

    def test_application_flow_does_not_request_or_store_discord_avatars(self):
        route_source = inspect.getsource(ws.public_apply_page)
        cookie_source = inspect.getsource(ws.set_apply_session_cookie)
        self.assertNotIn("cdn.discordapp.com", route_source)
        self.assertNotIn("avatar_url", route_source)
        self.assertNotIn("avatar", cookie_source)
        self.assertIn('("id", "username", "global_name")', cookie_source)

    def test_security_page_does_not_claim_public_discord_cdn_avatars(self):
        html = self.client.get("/sicherheit").text
        self.assertIn("keine Bilder, Skripte oder Stylesheets von Drittanbietern", html)
        self.assertIn("Interne Dashboard- und Teamansichten", html)
        self.assertNotIn("Die öffentliche Teamvorstellung kann Profilbilder", html)
    def test_nothing_is_invented_while_details_are_missing(self):
        html = self.client.get("/impressum").text
        self.assertIn("noch nicht zur Veröffentlichung freigegeben", html)
        self.assertIn("fehlt", html)
        self.assertFalse(legal.ready())

    def test_site_stays_out_of_search_engines_until_ready(self):
        self.assertEqual(self.client.get("/").headers["x-robots-tag"], "noindex, nofollow")
        self.assertIn("Disallow: /", self.client.get("/robots.txt").text)
        self.assertNotIn("Allow: /regelwerk", self.client.get("/robots.txt").text)

    def test_publication_requires_explicit_legal_review_and_terms_approval(self):
        with patch.dict(os.environ, LEGAL_ENV):
            self.assertTrue(legal.publication_ready())
        with patch.dict(os.environ, {**LEGAL_ENV, "PULSE_LEGAL_REVIEWED": "0"}):
            self.assertFalse(legal.publication_ready())
        with patch.dict(os.environ, {**LEGAL_ENV, "PULSE_TERMS_APPROVED": "0"}):
            self.assertFalse(legal.publication_ready())

    def test_terms_are_marked_as_draft_until_release(self):
        with patch.dict(os.environ, clean_env(), clear=True):
            html = self.client.get("/nutzungsbedingungen").text
        self.assertIn("Entwurf – noch nicht freigegeben", html)
        self.assertIn("nicht als abschließend geprüfte Vertragsfassung", html)
        self.assertIn("§ 305 Abs. 2 BGB", html)

    def test_complete_details_open_the_site(self):
        with patch.dict(os.environ, LEGAL_ENV):
            html = self.client.get("/impressum").text
            self.assertIn("Test Person", html)
            self.assertIn("Teststraße 1<br>12345 Teststadt", html)
            self.assertNotIn("noch nicht vollständig", html)
            self.assertEqual(self.client.get("/").headers["x-robots-tag"], "index, follow")
            robots = self.client.get("/robots.txt").text
            self.assertIn("Allow: /regelwerk$", robots)
            self.assertIn("Allow: /team-vorstellung$", robots)
            self.assertIn("Sitemap:", robots)
            self.assertEqual(self.client.get("/sitemap.xml").status_code, 200)
            self.assertIn("/team-vorstellung", self.client.get("/sitemap.xml").text)
            self.assertIn("Disallow: /", robots)

    def test_impressum_distinguishes_mstv_and_ddg_scope(self):
        html = self.client.get("/impressum").text
        self.assertIn("§ 18 Abs. 1 MStV", html)
        self.assertIn("§ 5 DDG", html)
        self.assertIn("geschäftsmäßig und in der Regel gegen Entgelt", html)
        self.assertIn("bürgerliche Vor- und Nachname", html)
        self.assertIn("§ 18 Abs. 2 MStV", html)

    def test_publication_is_blocked_until_ddg_and_mstv_18_2_scope_is_assessed(self):
        env = dict(LEGAL_ENV)
        env.pop("PULSE_LEGAL_DDG_ASSESSMENT", None)
        env.pop("PULSE_LEGAL_MSTV_18_2_ASSESSMENT", None)
        with patch.dict(os.environ, env, clear=True):
            blockers = legal.publication_blockers()
        self.assertIn("PULSE_LEGAL_DDG_ASSESSMENT", blockers)
        self.assertIn("PULSE_LEGAL_MSTV_18_2_ASSESSMENT", blockers)

    def test_ddg_assessment_requires_second_contact_channel_when_applicable(self):
        with patch.dict(os.environ, {
            **LEGAL_ENV,
            "PULSE_LEGAL_DDG_ASSESSMENT": "applicable",
            "PULSE_LEGAL_PHONE": "",
            "PULSE_LEGAL_CONTACT_METHOD_URL": "",
        }):
            blockers = legal.publication_blockers()
        self.assertIn("PULSE_LEGAL_CONTACT_METHOD_URL", blockers)
        with patch.dict(os.environ, {
            **LEGAL_ENV,
            "PULSE_LEGAL_DDG_ASSESSMENT": "applicable",
            "PULSE_LEGAL_PHONE": "+49 123 456789",
        }):
            self.assertNotIn("PULSE_LEGAL_CONTACT_METHOD_URL", legal.publication_blockers())

    def test_valid_https_contact_method_is_displayed_when_ddg_applies(self):
        with patch.dict(os.environ, {
            **LEGAL_ENV,
            "PULSE_LEGAL_DDG_ASSESSMENT": "applicable",
            "PULSE_LEGAL_CONTACT_METHOD_URL": "https://example.invalid/contact",
            "PULSE_LEGAL_CONTACT_METHOD_LABEL": "Kontaktformular",
        }):
            html = legal.impressum_html()
        self.assertIn("Kontaktformular", html)
        self.assertIn('href="https://example.invalid/contact"', html)

    def test_non_https_contact_method_does_not_satisfy_ddg_gate(self):
        with patch.dict(os.environ, {
            **LEGAL_ENV,
            "PULSE_LEGAL_DDG_ASSESSMENT": "applicable",
            "PULSE_LEGAL_PHONE": "",
            "PULSE_LEGAL_CONTACT_METHOD_URL": "javascript:alert(1)",
        }):
            blockers = legal.publication_blockers()
            html = legal.impressum_html()
        self.assertIn("PULSE_LEGAL_CONTACT_METHOD_URL", blockers)
        self.assertNotIn('href="javascript:alert(1)"', html)

    def test_mstv_18_2_requires_responsible_name_and_address_when_applicable(self):
        with patch.dict(os.environ, {
            **LEGAL_ENV,
            "PULSE_LEGAL_MSTV_18_2_ASSESSMENT": "applicable",
            "PULSE_LEGAL_CONTENT_RESPONSIBLE_NAME": "",
            "PULSE_LEGAL_CONTENT_RESPONSIBLE_ADDRESS": "",
        }):
            blockers = legal.publication_blockers()
        self.assertIn("PULSE_LEGAL_CONTENT_RESPONSIBLE_NAME", blockers)
        self.assertIn("PULSE_LEGAL_CONTENT_RESPONSIBLE_ADDRESS", blockers)

    def test_optional_imprint_details_are_rendered_only_when_supplied_and_escaped(self):
        env = {
            **LEGAL_ENV,
            "PULSE_LEGAL_FORM": "Verein",
            "PULSE_LEGAL_REPRESENTATIVE": "<script>evil()</script>",
            "PULSE_LEGAL_REGISTER": "VR 123",
        }
        with patch.dict(os.environ, env):
            html = legal.impressum_html()
        self.assertIn("Verein", html)
        self.assertIn("VR 123", html)
        self.assertIn("&lt;script&gt;evil()&lt;/script&gt;", html)
        self.assertNotIn("<script>evil()", html)

    def test_vionity_hoster_is_disclosed_without_claiming_a_signed_avv(self):
        html = legal.privacy_html()
        for term in (
            "Henrik Kramer e.K.",
            "Prepaid-Hoster.de",
            "Vionity-Kundenportal",
            "Kurpromenade 48",
            "23743 Grömitz",
            "Offenbach am Main",
            "Ein für diesen VPS abgeschlossener und unterzeichneter AVV ist derzeit nicht nachgewiesen",
        ):
            self.assertIn(term, html)
        # The hosting provider is a privacy recipient, not BOCHUM RP's website operator.
        self.assertNotIn("Henrik Kramer e.K.", legal.impressum_html())
        self.assertNotIn("PULSE_LEGAL_HOSTER", legal.missing_fields())
        with patch.dict(os.environ, {"PULSE_LEGAL_HOSTER": "Vionity"}):
            self.assertIn("Henrik Kramer e.K.", legal.privacy_html())

    def test_privacy_controller_and_optional_dpo_are_separately_configurable(self):
        with patch.dict(os.environ, {
            **LEGAL_ENV,
            "PULSE_PRIVACY_CONTROLLER_NAME": "Datenschutz-Verantwortlicher",
            "PULSE_PRIVACY_CONTROLLER_ADDRESS": "Datenschutzweg 2|54321 Beispielort",
            "PULSE_PRIVACY_CONTROLLER_EMAIL": "privacy@example.invalid",
            "PULSE_PRIVACY_DPO_CONTACT": "DPO, dpo@example.invalid",
            "PULSE_PRIVACY_SUPERVISORY_AUTHORITY": "Zuständige Landesdatenschutzbehörde",
            "PULSE_PRIVACY_TRANSFER_INFO": "Verifizierte Transferinformation",
        }):
            html = legal.privacy_html()
        for term in ("Datenschutz-Verantwortlicher", "Datenschutzweg 2", "privacy@example.invalid",
                     "DPO, dpo@example.invalid", "Zuständige Landesdatenschutzbehörde",
                     "Verifizierte Transferinformation"):
            self.assertIn(term, html)

    def test_approved_terms_cover_core_community_topics_and_no_blanket_liability_exclusion(self):
        with patch.dict(os.environ, LEGAL_ENV):
            html = legal.terms_html()
        for term in ("Konten und Zugang", "Zulässige Nutzung", "Inhalte und Rechte",
                     "Bewerbungen und Teammitgliedschaft", "Moderation und Maßnahmen",
                     "KI-Funktionen", "Externe Plattformen", "Haftung", "Datenschutz"):
            self.assertIn(term, html)
        self.assertIn("Es gelten die gesetzlichen Haftungsregelungen", html)
        self.assertNotIn("jegliche Haftung ausgeschlossen", html)

    def test_cookie_notice_does_not_claim_every_storage_entry_is_exempt(self):
        html = self.client.get("/cookies").text
        self.assertIn("gesondert anhand ihrer objektiven Erforderlichkeit", html)
        self.assertNotIn("Alle Einträge oben sind für die jeweilige Funktion unbedingt erforderlich", html)

    def test_operator_values_are_escaped(self):
        with patch.dict(os.environ, {**LEGAL_ENV, "PULSE_LEGAL_NAME": "<script>"}):
            self.assertNotIn("<script>", legal.impressum_html())

    def test_privacy_names_every_processing_and_each_required_fact(self):
        html = self.client.get("/datenschutz").text
        for term in ("Discord OAuth", "IP-Adresse", "Bewerbung", "Öffentliche Teamprofile", "Profilbild", "ausdrücklich zugestimmt", "Einwilligung", "teamseite_freigabe_widerrufen",
                     "bis zu acht vorherige Gesprächsnachrichten", "/ki_vergessen", "Art. 14 DSGVO", "KI-Anbieter", "Backups", "Art. 77", "Art. 21"):
            self.assertIn(term, html)
        self.assertEqual(html.count("<dt>Rechtsgrundlage</dt>"), len(legal.SECTIONS))
        self.assertEqual(html.count("<dt>Speicherdauer</dt>"), len(legal.SECTIONS))
        self.assertNotIn("keine personenbezogenen Daten", html)
        self.assertNotIn("DSGVO-konform", html)

    def test_privacy_periods_match_the_code(self):
        import pulse_backup
        import pulse_ip_audit
        import pulse_sessions
        self.assertEqual(pulse_ip_audit.KEEP_DAYS, 30)
        self.assertEqual(ws.SESSION_DAYS, 7)
        self.assertEqual(pulse_sessions.IDLE_SECONDS, 24 * 3600)
        keep = pulse_backup.retention_keep.__defaults__
        self.assertEqual(keep, (7, 4, 6))

    def test_cookie_table_lists_every_cookie_the_code_sets(self):
        source = "".join(Path(name).read_text(encoding="utf-8") for name in ("webserver.py", "pulse_admin.py"))
        names = set(re.findall(r'set_cookie\(\s*"([a-z_]+)"', source))
        table = " ".join(row[0] for row in legal.STORAGE)
        self.assertTrue(names)
        for name in names:
            self.assertIn(name, table)

    def test_public_pages_store_nothing(self):
        script = Path("static/public.js").read_text(encoding="utf-8")
        self.assertNotIn("document.cookie", script)
        # Local storage is only reached from the consent flow, which needs optional services.
        self.assertLess(script.index("function startConsent"), script.index('getElementById("consent-config")'))


class Consent(Base):
    SERVICE = ({"id": "map", "label": "Karte", "purpose": "Kartenansicht", "provider": "Beispiel"},)

    def test_no_banner_without_optional_services(self):
        html = self.client.get("/cookies").text
        self.assertNotIn("consent-config", html)
        self.assertIn("keine Analyse", html)
        self.assertNotIn("data-consent-open", html)

    def test_banner_config_only_with_optional_services(self):
        with patch.object(site, "OPTIONAL_SERVICES", self.SERVICE):
            html = self.client.get("/cookies").text
        self.assertIn('id="consent-config"', html)
        self.assertIn("data-consent-open", html)

    def test_banner_offers_equal_choices_and_preselects_nothing(self):
        script = Path("static/public.js").read_text(encoding="utf-8")
        for label in ("Alle akzeptieren", "Nur notwendige", "Einstellungen"):
            self.assertIn(label, script)
        self.assertIn("box.checked = !!(saved && saved.choices[service.id])", script)
        self.assertEqual(script.count('element.className = "btn btn-line"'), 1)  # one look for all buttons


class Exposure(Base):
    def test_health_is_minimal_for_visitors(self):
        for host, expected in (("8.8.8.8", False), ("2001:4860:4860::8888", False), ("127.0.0.1", True),
                               ("172.30.0.1", True), (None, True), ("testclient", True)):
            self.assertEqual(pulse_security.is_internal_client(host), expected, host)
        client = TestClient(ws.app, raise_server_exceptions=False, client=("8.8.8.8", 4000))
        self.assertEqual(set(client.get("/healthz").json()), {"ok"})
        self.assertNotIn("version", client.get("/ultimate/status").json())

    def test_health_stays_complete_for_docker(self):
        client = TestClient(ws.app, raise_server_exceptions=False, client=("127.0.0.1", 4000))
        self.assertIn("version", client.get("/healthz").json())

    def test_caddy_hides_health_data(self):
        text = Path("deploy/Caddyfile").read_text(encoding="utf-8")
        self.assertIn("respond @internal 404", text)


if __name__ == "__main__":
    unittest.main()
