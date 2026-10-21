"""A-03: one permission model (deny by default) and a route matrix (no login → no content, no right → 403)."""
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_permissions as perms
import webserver as ws

TEAM, OTHER, OLD_TEAM = 100, 200, 300


def member(*role_ids, admin=False, uid=7):
    return SimpleNamespace(id=uid, roles=[SimpleNamespace(id=r, name=f"Rolle {r}") for r in role_ids],
                           guild_permissions=SimpleNamespace(administrator=admin))


def guild_with(m):
    return SimpleNamespace(owner_id=1, get_member=lambda uid: m if uid == m.id else None)


CONFIG = {
    "team_role_ids": [TEAM],
    "permissions": {str(OTHER): {"can_manage_announcements": True, "can_warn": True},
                    str(OLD_TEAM): {"can_moderate_bans": True}},
    "dashboard_roles": {"p1": {"name": "Support-Profil", "discord_role_ids": [TEAM], "permissions": {"can_manage_tickets": True}},
                        "p2": {"name": "Fremdprofil", "discord_role_ids": [OTHER], "permissions": {"can_promote": True}}},
}


class PermissionModelTests(unittest.TestCase):
    def compute(self, m, config=CONFIG):
        return perms.compute(guild_with(m), m.id, config)[0]

    def test_non_team_roles_grant_nothing(self):
        # regression: the former extra-permission patch applied config.permissions of *every* role
        p = self.compute(member(OTHER, OLD_TEAM))
        self.assertFalse(any(v for k, v in p.items()))

    def test_unconfigured_team_role_gets_dashboard_only(self):  # E-4
        p = self.compute(member(TEAM), {"team_role_ids": [TEAM]})
        self.assertTrue(p["can_view_dashboard"])
        self.assertFalse(p["can_view_analytics"])

    def test_profiles_only_through_team_roles_and_inheritance(self):
        p = self.compute(member(TEAM, OTHER))
        self.assertTrue(p["can_view_dashboard"])     # unconfigured team role default
        self.assertTrue(p["can_manage_tickets"])     # profile mapped to the team role
        self.assertFalse(p["can_promote"])           # profile mapped only to a non-team role
        lead = self.compute(member(TEAM), {"team_role_ids": [TEAM], "permissions": {str(TEAM): {"can_promote": True}}})
        for key in perms.INHERITED["can_promote"]:
            self.assertTrue(lead[key], key)

    def test_admin_and_unknown_member(self):
        p = self.compute(member(admin=True))
        self.assertTrue(p["is_admin"] and all(p[k] for k in perms.ALL_KEYS))
        none, m = perms.compute(SimpleNamespace(owner_id=1, get_member=lambda uid: None), 5, CONFIG)
        self.assertIsNone(m)
        self.assertFalse(any(none.values()))

    def test_explain_names_the_origin(self):
        info = perms.explain(guild_with(member(TEAM)), 7, CONFIG)
        self.assertIn("Dashboard-Profil Support-Profil", info["permissions"]["can_manage_tickets"])
        self.assertEqual(info["permissions"]["can_warn"], [])

    def test_permission_keys_are_maintained_in_one_place(self):
        import pulse_pro
        self.assertEqual(tuple(ws.PERM_KEYS), perms.BASE_KEYS)
        self.assertEqual(tuple(pulse_pro.EXTRA_PERMS), perms.EXTRA_KEYS)

    def test_webserver_uses_the_model_without_runtime_patch(self):
        m = member(OTHER)
        self.assertEqual(ws.compute_perms(guild_with(m), m.id, CONFIG)[0], self.compute(m))
        import pulse_pro
        self.assertNotIn("patched_compute", Path(pulse_pro.__file__).read_text(encoding="utf-8"))


PUBLIC_GET = {"/", "/favicon.ico", "/callback", "/apply", "/health", "/healthz", "/manifest.webmanifest", "/sw.js",
              "/robots.txt", "/ultimate/status", "/apply/callback", "/apply/login", "/login",
              "/logout"}  # U-13: GET /logout only shows a confirmation; logging out is POST
# Write routes that are public on purpose: the application portal (own session check inside) and the
# central /action handler (unknown actions are refused with 400 before login; real ones: see test).
PUBLIC_WRITE = {"/apply/keepalive", "/action"}
ADMIN_ONLY = ("/admin", "/admin/discord", "/admin/privacy", "/backups", "/settings", "/system")


class RouteMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import cogs.pulse_suite as suite
        cls.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        with patch.object(suite, "DB_PATH", os.path.join(cls.tmp.name, "suite.db")):
            suite.register_routes()
        cls.client = TestClient(ws.app, raise_server_exceptions=False)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_no_login_never_returns_content(self):
        leaks = []
        for route in ws.app.routes:
            path, methods = getattr(route, "path", ""), getattr(route, "methods", set()) or set()
            if "GET" not in methods or "{" in path or path in PUBLIC_GET or path.startswith("/static"):
                continue
            status = self.client.get(path, follow_redirects=False).status_code
            if status not in (302, 303, 307, 401, 403, 422, 503):
                leaks.append((path, status))
        self.assertEqual(leaks, [], "Seiten ohne Login erreichbar")

    def test_no_login_never_changes_anything(self):
        """Every write route, called with complete dummy form data but without a session, must refuse."""
        import re
        accepted = []
        for route in ws.app.routes:
            methods = (getattr(route, "methods", set()) or set()) & {"POST", "PUT", "PATCH", "DELETE"}
            if not methods or route.path in PUBLIC_WRITE:
                continue
            dependant = getattr(route, "dependant", None)
            data, files = {}, {}
            for param in (dependant.body_params if dependant else []):
                if "UploadFile" in str(getattr(param.field_info, "annotation", "")):
                    files[param.alias] = ("x.txt", b"x", "text/plain")
                else:
                    data[param.alias] = "1"
            query = {p.alias: "1" for p in (dependant.query_params if dependant else [])}
            path = re.sub(r"\{[^}]+\}", "1", route.path)
            for method in methods:
                status = self.client.request(method, path, data=data or None, files=files or None,
                                             params=query or None, follow_redirects=False).status_code
                if status not in (302, 303, 307, 401, 403):
                    accepted.append((method, route.path, status))
        self.assertEqual(accepted, [], "schreibende Route ohne Login nicht abgewiesen")

    def test_action_with_real_action_requires_login(self):
        for action in ("warn_with_proof", "remove_warn", "promote", "save_team_roles", "add_note"):
            status = self.client.post("/action", data={"action": action, "user_id": "1"}, follow_redirects=False).status_code
            self.assertIn(status, (303, 401, 403), action)

    def test_admin_pages_refuse_plain_team_members(self):
        from tests.test_pulse_admin_area import TEAMLER, fake_auth_for
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            for path in ADMIN_ONLY:
                self.assertEqual(self.client.get(path, follow_redirects=False).status_code, 403, path)

    def test_parametrised_api_requires_login(self):
        for path in ("/api/roblox-user?username=Builderman", "/api/roblox-search?query=abc"):
            self.assertIn(self.client.get(path, follow_redirects=False).status_code, (303, 401), path)


if __name__ == "__main__":
    unittest.main()
