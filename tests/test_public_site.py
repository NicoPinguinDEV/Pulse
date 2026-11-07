"""Public website: pages, separation from the dashboard, legal state, consent and exposure."""
import base64
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_public_legal as legal
import pulse_public_site as site
import pulse_security
import webserver as ws

LEGAL_ENV = {
    "PULSE_LEGAL_NAME": "Test Person", "PULSE_LEGAL_ADDRESS": "Teststraße 1|12345 Teststadt",
    "PULSE_LEGAL_EMAIL": "kontakt@example.invalid", "PULSE_LEGAL_HOSTER": "Test Hosting GmbH",
    "PULSE_LEGAL_REVIEWED": "1", "PULSE_TERMS_APPROVED": "1", "PULSE_TERMS_EFFECTIVE_DATE": "2026-11-06",
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
            self.assertEqual(re.findall(r'<(?:script|link|img)[^>]+(?:src|href)="https?://', html), [], path)
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

    def test_public_team_profiles_are_grouped_by_rank_level(self):
        import pulse_public_content as content
        profiles = (
            ("Nico", "Founder"),
            ("Mara", "Teamleitung"),
            ("Lena", "Admin"),
            ("Finn", "Moderator"),
            ("Tom", "Support-Leitung"),
        )
        for name, rank in profiles:
            profile_id = content.create_profile(name, rank)
            self.assertTrue(content.set_published(profile_id, True))

        html = self.client.get("/team-vorstellung").text
        for name in ("Nico", "Mara", "Lena", "Finn", "Tom"):
            self.assertIn(name, html)
        self.assertIn("Inhaber- &amp; Führungsebene", html)
        self.assertIn("Management- &amp; Teamleitungsebene", html)
        self.assertIn("Administrationsebene", html)
        self.assertIn("Moderationsebene", html)
        self.assertIn("Supportebene", html)
        self.assertLess(html.index("Inhaber- &amp; Führungsebene"), html.index("Management- &amp; Teamleitungsebene"))
        self.assertLess(html.index("Management- &amp; Teamleitungsebene"), html.index("Administrationsebene"))

    def test_public_site_pointer_effect_is_progressive_and_respects_reduced_motion(self):
        public_css = (Path(site.ROOT) / "static" / "public.css").read_text(encoding="utf-8")
        public_js = (Path(site.ROOT) / "static" / "public.js").read_text(encoding="utf-8")
        self.assertIn("(pointer: fine)", public_css)
        self.assertIn("prefers-reduced-motion: no-preference", public_css)
        self.assertIn("prefers-reduced-motion: reduce", public_css)
        self.assertIn("function startPointerEffects()", public_js)
        self.assertIn("startPointerEffects();", public_js)

    def test_public_team_profiles_are_hidden_until_explicitly_published(self):
        import pulse_public_content as content
        profile_id = content.create_profile("Mara", "Support-Leitung", "Hilft bei Fragen.")
        draft_page = self.client.get("/team-vorstellung").text
        self.assertNotIn("Mara", draft_page)
        self.assertTrue(content.set_published(profile_id, True))
        response = self.client.get("/team-vorstellung")
        self.assertNotIn("public", response.headers.get("cache-control", "").lower())
        public_page = response.text
        self.assertIn("Mara", public_page)
        self.assertIn("Support-Leitung", public_page)
        self.assertIn("Hilft bei Fragen.", public_page)
        for internal_field in ("discord_id", "private_note", "moderation_case", "service_hours"):
            self.assertNotIn(internal_field, public_page.lower())
        self.assertTrue(content.set_published(profile_id, False))
        withdrawn_page = self.client.get("/team-vorstellung").text
        self.assertNotIn("Mara", withdrawn_page)


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

    def test_public_team_profile_requires_subject_consent_before_publish(self):
        import pulse_public_content as content
        self.login(True)
        profile_id = content.create_profile("Mara", "Support", "Freigegebener Text")
        denied = self.client.post(f"/admin/website/team/{profile_id}/publish", data={"published": "1"})
        self.assertEqual(denied.status_code, 422)
        self.assertNotIn("Mara", self.client.get("/team-vorstellung").text)
        approved = self.client.post(
            f"/admin/website/team/{profile_id}/publish",
            data={"published": "1", "consent_confirmed": "1"},
        )
        self.assertEqual(approved.status_code, 303)
        self.assertIn("Mara", self.client.get("/team-vorstellung").text)
        hidden = self.client.post(f"/admin/website/team/{profile_id}/publish", data={"published": "0"})
        self.assertEqual(hidden.status_code, 303)
        self.assertNotIn("Mara", self.client.get("/team-vorstellung").text)

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
        self.assertNotIn("data:image/png", self.client.get("/team-vorstellung").text)
        approved = self.client.post(
            f"/admin/website/team/{profile_id}/publish",
            data={"published": "1", "consent_confirmed": "1"},
        )
        self.assertEqual(approved.status_code, 303)
        public_html = self.client.get("/team-vorstellung").text
        self.assertIn('class="team-avatar"', public_html)
        self.assertIn("data:image/png;base64,", public_html)
        self.assertIn('alt="Porträt von Nico"', public_html)
        withdrawn = self.client.post(
            f"/admin/website/team/{profile_id}/publish", data={"published": "0"}
        )
        self.assertEqual(withdrawn.status_code, 303)
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
        self.assertIn("keine abschließend geprüften Vertragsbedingungen", html)

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

    def test_operator_values_are_escaped(self):
        with patch.dict(os.environ, {**LEGAL_ENV, "PULSE_LEGAL_NAME": "<script>"}):
            self.assertNotIn("<script>", legal.impressum_html())

    def test_privacy_names_every_processing_and_each_required_fact(self):
        html = self.client.get("/datenschutz").text
        for term in ("Discord OAuth", "IP-Adresse", "Bewerbung", "Öffentliche Teamprofile", "Profilbild", "Einwilligung der betroffenen Person",
                     "KI-Anbieter", "Backups", "Art. 77", "Art. 21"):
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
