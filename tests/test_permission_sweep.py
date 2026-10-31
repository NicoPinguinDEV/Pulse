"""Phase 8: authenticated low-privilege sweep over every write route.

A team member who may only *view* the dashboard (no extra permission, no admin) calls every POST/PUT/PATCH/DELETE
route directly – with complete dummy form data, like a hand-made request that bypasses the UI. Only the
personal/team-wide actions listed in ALLOWED may succeed; every other route must refuse (401/403/422, error
redirect or 4xx). A new route that forgets its server-side permission check makes this test fail.

Fakes only: temporary databases, JSON writes swallowed, no Discord, no network.
"""
import os
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_permissions
import webserver as ws
from tests.fakes import FakeChannel, FakeGuild, FakeMember, FakeRole

# Personal or team-wide actions every team member may do on purpose (own inbox, own status, own sessions,
# shift handovers, ideas/votes, reading announcements, the read-only copilot).
ALLOWED = {
    "/logout",
    "/me/sessions/revoke-all",
    "/pulse-inbox/read", "/pulse-inbox/read/{nid}", "/pulse-inbox/settings",
    "/team-status/set",
    "/handover/create", "/ultimate/handovers/create",
    "/ultimate/announcements/{announcement_id}/read",
    "/ultimate/ideas/create", "/ultimate/ideas/{idea_id}/vote",
    "/ultimate/copilot", "/ultimate/feedback/create",
    "/me/month/optin",
}
# Routes that refuse the dummy request with a validation answer (unknown id, invalid value) instead of 403 – their
# permission check sits after the lookup or they are personal actions. They still must never *succeed* here.
INPUT_CHECKED = {
    "/action", "/shift/action", "/log/create", "/calendar/create", "/tasks/update",
    "/me/start-page", "/me/sessions/revoke", "/me/sessions/revoke-others",
    "/training/{training_id}/submit", "/ultimate/polls/{poll_id}/vote", "/ultimate/handovers/{handover_id}/close",
    "/ultimate/team/status", "/recovery/authorize/start", "/suite/task/{task_id}/meta", "/suite/tasks/{task_id}/move",
}
JSON_STORES = ("DATA_FILE", "CONFIG_FILE", "APPS_FILE", "SHIFTS_FILE", "LOGS_FILE", "AUDIT_FILE", "MEETINGS_FILE")


class LowPrivilegeWriteSweep(unittest.TestCase):
    def setUp(self):
        import cogs.pulse_suite as suite
        import pulse_next
        import pulse_ultimate
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        tmp = Path(self.tmp.name)
        team = FakeRole(100, "Team")
        self.me = FakeMember(7, "Tess", roles=[team])
        self.other = FakeMember(8, "Olli", roles=[team])
        guild = FakeGuild(gid=ws.GUILD_ID, members=[self.me, self.other], roles=[team], channels=[FakeChannel(555)])
        guild.chunked = True
        perms = {k: False for k in pulse_permissions.ALL_KEYS}
        perms.update(can_view_dashboard=True, is_admin=False)
        self.writes = []
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(tmp / "pulse.db")),
            patch.object(suite, "DB_PATH", str(tmp / "suite.db")),
            patch.object(ws, "BASE_DIR", tmp),
            patch.object(ws, "get_current_user", lambda _s: {"id": "7", "global_name": "Tess", "username": "tess"}),
            patch.object(ws, "compute_perms", lambda _g, _u, _c: (dict(perms), self.me)),
            patch.object(ws, "load_config", lambda *_a, **_k: {"team_role_ids": [100]}),
            patch.object(ws, "save_json", lambda path, data: self.writes.append(path)),
            patch.object(ws, "log_audit", lambda *_a, **_k: None),
            patch.object(pulse_ultimate, "_INIT", False),
            patch.object(pulse_next, "_INIT", False),
            *[patch.object(ws, name, str(tmp / getattr(ws, name))) for name in JSON_STORES],
        ]
        for p in self.patches:
            p.start()
        # another member's ban entry with the dummy id: /log/edit and /log/delete must reach their permission check
        (tmp / "logs.json").write_text('[{"id": "8", "type": "Ban", "target_user": "Olli", "reason": "x"}]', encoding="utf-8")
        pulse_db.init_db()
        pulse_ultimate.init_db()
        pulse_next.setup_next()
        suite.init_db()
        suite.register_routes()
        import pulse_flags  # switched-off functions answer 404 before their permission check – sweep them all
        for key in pulse_flags.FEATURES:
            if key != "maintenance":
                pulse_flags.set_enabled(key, True)
        self.bot_before = getattr(ws.app.state, "bot", None)
        ws.app.state.bot = SimpleNamespace(get_guild=lambda _gid: guild, user=SimpleNamespace(id=999), latency=0.1,
                                           is_ready=lambda: True, guilds=[guild], get_channel=lambda _c: None)
        ws._rate_hits.clear()
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers={"Origin": "http://testserver"})

    def tearDown(self):
        import pulse_flags
        ws.app.state.bot = self.bot_before
        for p in reversed(self.patches):
            p.stop()
        pulse_flags.invalidate()
        self.tmp.cleanup()

    def _call(self, route, method):
        dependant = getattr(route, "dependant", None)
        data, files = {}, {}
        for param in (dependant.body_params if dependant else []):
            annotation = str(getattr(param.field_info, "annotation", ""))
            if "UploadFile" in annotation:
                files[param.alias] = ("x.txt", b"x", "text/plain")
            elif "bool" in annotation:
                data[param.alias] = "true"  # a valid value, so 422 never hides the permission check
            else:
                data[param.alias] = "8"  # an existing other member / plausible id
        query = {p.alias: "8" for p in (dependant.query_params if dependant else [])}
        path = re.sub(r"\{[^}]+\}", "8", route.path)
        return self.client.request(method, path, data=data or None, files=files or None, params=query or None,
                                   follow_redirects=False)

    def test_view_only_member_cannot_use_protected_write_routes(self):
        succeeded, crashed, not_forbidden, checked = set(), [], [], 0
        for route in ws.app.routes:
            methods = (getattr(route, "methods", set()) or set()) & {"POST", "PUT", "PATCH", "DELETE"}
            for method in sorted(methods):
                checked += 1
                response = self._call(route, method)
                status, location = response.status_code, response.headers.get("location", "")
                if status >= 500:
                    crashed.append((method, route.path, status))
                elif (200 <= status < 300) or (status in (302, 303, 307) and "t=err" not in location):
                    succeeded.add(route.path)
                elif status not in (401, 403) and route.path not in ALLOWED | INPUT_CHECKED:
                    not_forbidden.append((method, route.path, status))
        self.assertGreater(checked, 150, "Sweep hat die Routen nicht gefunden")
        self.assertEqual(crashed, [], "Route stürzt bei direktem Aufruf ab (500) statt sauber abzulehnen")
        self.assertEqual(not_forbidden, [], "Geschützte Route lehnt nicht mit 401/403 ab (Rechteprüfung fehlt oder kommt zu spät)")
        self.assertEqual(sorted(succeeded - ALLOWED), [],
                         "Schreibende Route ohne serverseitige Rechteprüfung (nur Dashboard-Ansicht genügt)")
        self.assertFalse(any(os.path.basename(str(p)) in {"config.json"} for p in self.writes),
                         "config.json wurde von einem Teammitglied ohne Rechte geändert")
        self.assertEqual(self.other.removed_roles + self.other.added_roles, [], "Rollen eines anderen Mitglieds geändert")


if __name__ == "__main__":
    unittest.main()
