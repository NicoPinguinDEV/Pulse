"""Server Control Center: permissions, input validation, no shell, no secrets, log streaming, parallelism,
deploy/rollback safety of the host agent and backup verification.

No Discord connection, no Docker, no git network access: the host agent runs with a fake process runner,
the dashboard routes with a fake login (same pattern as tests/test_pulse_admin_area.py).
"""
import asyncio
import importlib.util
import json
import os
import re
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_backup
import pulse_db
import pulse_live_console
import pulse_permissions
import pulse_server_control as scc
import webserver as ws
from pulse_admin import SERVER_SECTION_KEYS, admin_sections, can_open_admin

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("pulse_host_agent", ROOT / "deploy" / "pulse_host_agent.py")
agent_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_mod)

TOKEN = "t" * 40
SHA_OLD = "a" * 40
SHA_NEW = "b" * 40
SHA_OLDER = "c" * 40
# Made-up token shape for the redaction tests, assembled at runtime so secret scanners do not flag the source.
DISCORD_LIKE = ".".join(("MTIzNDU2Nzg5" + "MDEyMzQ1Njc4OQ", "GaBcDe", "abcdefghijklmn" + "opqrstuvwxyz0123"))
GUILD = SimpleNamespace(id=1, name="© BOCHUM RP | V1", members=[], roles=[], channels=[], text_channels=[], member_count=10,
                        owner_id=0, icon=None, get_member=lambda _: None, get_role=lambda _: None, get_channel=lambda _: None)


def fake_auth_for(perms):
    def fake_auth(request, session, perm="can_view_dashboard", admin=False):
        if admin and not perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="Dieser Bereich ist nur für Administratoren.")
        if perm and not perms.get(perm):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        return SimpleNamespace(user={"id": "123", "global_name": "Tester"}, guild=GUILD, guild_name=GUILD.name,
                               perms=perms, config={"team_role_ids": []}, member=None, msg="", msg_ok=True)
    return fake_auth


ALL = pulse_permissions.ALL_KEYS
ADMIN = dict({k: True for k in ALL}, is_admin=True)
TEAMLER = dict({k: False for k in ALL}, can_view_dashboard=True, can_manage_tickets=True, is_admin=False)
SERVER_OPERATOR = dict(TEAMLER, can_manage_server=True)

ROUTES = [
    ("get", "/admin/server", None), ("get", "/admin/server/api/overview", None), ("get", "/admin/server/api/container", None),
    ("get", "/admin/server/api/deploy", None), ("get", "/admin/server/api/backups", None), ("get", "/admin/server/api/diagnose", None),
    ("get", "/admin/server/api/health", None),
    ("get", "/admin/server/api/jobs/0123456789ab", None), ("get", "/admin/server/api/logs/stream", None),
    ("post", "/admin/server/api/console", {"command": "help"}), ("post", "/admin/server/api/deploy/fetch", {}),
    ("post", "/admin/server/api/actions", {"action": "restart", "confirm": "NEUSTART"}),
    ("post", "/admin/server/api/jobs/0123456789ab/cancel", {}), ("post", "/admin/server/api/backups/verify", {"filename": "x"}),
]


# ----------------------------------------------------------------------------- fakes for the host agent
class FakeRunner:
    """Answers the fixed git/docker argument lists like a real VPS would."""

    def __init__(self, head=SHA_OLD, origin=SHA_NEW, branch="main", status="", ancestor=True, stream_lines=(), stream_code=0,
                 fetch_ok=True, after_stream_head=None):
        self.head, self.origin, self.branch, self.status = head, origin, branch, status
        self.ancestor, self.stream_lines, self.stream_code, self.fetch_ok = ancestor, list(stream_lines), stream_code, fetch_ok
        self.after_stream_head = after_stream_head
        self.calls, self.streams = [], []

    def run(self, args, cwd=None, timeout=30, env=None):
        assert isinstance(args, list) and all(isinstance(a, str) for a in args)
        self.calls.append(args)
        if args[0] == "git":
            g = args[3:]
            if g[:1] == ["fetch"]:
                return (0, "", "") if self.fetch_ok else (128, "", "fatal: unable to access")
            if g[:2] == ["branch", "--show-current"]:
                return 0, self.branch + "\n", ""
            if g[:2] == ["log", "-1"] and g[-1].startswith("--format=%H"):
                return 0, f"{self.head}\x1fAktueller Stand\x1f2026-10-23T10:00:00+02:00\n", ""
            if g[:1] == ["status"]:
                return 0, self.status, ""
            if g[:2] == ["rev-parse", "--verify"]:
                return (0, self.origin + "\n", "") if self.origin else (1, "", "")
            if g[:2] == ["rev-parse", "HEAD"]:
                return 0, self.head + "\n", ""
            if g[:2] == ["log", "-1"]:
                return 0, "Neuer Stand\x1f2026-10-23T12:00:00+02:00\n", ""
            if g[:1] == ["rev-list"]:
                return 0, "0\t2\n", ""
            if g[:1] == ["merge-base"]:
                return (0, "", "") if self.ancestor else (1, "", "")
            if g[:1] == ["log"] and "--first-parent" in g:
                return 0, f"{SHA_OLDER}\x1fÄlterer Stand\x1f2026-10-20T10:00:00+02:00\n", ""
            if g[:1] == ["log"]:
                return 0, f"{SHA_NEW}\x1fNeue Funktion\x1fNico\x1f2026-10-23T12:00:00+02:00\n", ""
            if g[:1] == ["diff"]:
                return 0, "10\t2\twebserver.py\n", ""
        if args[:2] == ["docker", "inspect"]:
            state = {"Status": "running", "Running": True, "Health": {"Status": "healthy", "Log": [{"Output": f"TOKEN={DISCORD_LIKE}"}]}}
            return 0, json.dumps(state) + "\t0\t2026-10-23T09:00:00Z", ""
        if args[:2] == ["docker", "stats"]:
            return 0, json.dumps({"CPUPerc": "1.5%", "MemUsage": "180MiB / 512MiB", "MemPerc": "35%"}), ""
        if args[:2] == ["docker", "logs"]:
            return 0, f"2026-10-23T10:00:00.000000001Z Bot gestartet\n2026-10-23T10:00:01.000000001Z DISCORD_TOKEN={DISCORD_LIKE}\n", ""
        return 1, "", "unerwarteter Aufruf"

    def stream(self, args, cwd=None, env=None, on_line=None, cancel=None, can_cancel=None, timeout=1800):
        self.streams.append(args)
        for line in self.stream_lines:
            if callable(line):
                line()
                continue
            on_line(line)
        if self.after_stream_head:
            self.head = self.after_stream_head
        return self.stream_code


def make_agent(tmp, runner, healthy=True, health_sequence=None):
    env = {"PULSE_AGENT_TOKEN": TOKEN, "PULSE_REPO_DIR": tmp, "PULSE_AGENT_STATE": str(Path(tmp) / "state.json"),
           "PULSE_AGENT_BIND": "127.0.0.1"}
    (Path(tmp) / ".git").mkdir(exist_ok=True)
    (Path(tmp) / "deploy").mkdir(exist_ok=True)
    (Path(tmp) / "deploy" / "remote_deploy.sh").write_text("#!/bin/bash\n")
    (Path(tmp) / "deploy" / "rollback_code.sh").write_text("#!/bin/bash\n")
    clock = {"t": 0.0}
    sequence = list(health_sequence or [])

    def http_get(url, timeout=6):
        ok = sequence.pop(0) if sequence else healthy
        return (200, '{"ok": true, "version": "7.17.0"}') if ok else (503, '{"ok": false}')

    def sleep(seconds):
        clock["t"] += seconds

    return agent_mod.Agent(agent_mod.Config(env), runner=runner, http_get=http_get, sleep=sleep, clock=lambda: clock["t"])


def wait_done(agent, job_id, seconds=5):
    deadline = time.time() + seconds
    while time.time() < deadline:
        job = agent.public_job(agent.find_job(job_id))
        if job["status"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("Vorgang wurde nicht beendet")


# ----------------------------------------------------------------------------- permissions & navigation
class PermissionModelTests(unittest.TestCase):
    def member(self, role_ids, member_id=5):
        roles = [SimpleNamespace(id=r, name=f"Rolle {r}") for r in role_ids]
        return SimpleNamespace(id=member_id, roles=roles, guild_permissions=SimpleNamespace(administrator=False))

    def guild_with(self, member):
        return SimpleNamespace(owner_id=1, get_member=lambda _id: member)

    def test_deny_by_default_for_team_roles(self):
        member = self.member([10])
        perms, _ = pulse_permissions.compute(self.guild_with(member), 5, {"team_role_ids": [10], "permissions": {}})
        self.assertFalse(perms["can_manage_server"])
        perms, _ = pulse_permissions.compute(self.guild_with(member), 5, {"team_role_ids": [10],
                                                                           "permissions": {"10": {"can_promote": True}}})
        self.assertFalse(perms["can_manage_server"], "Leitung erbt Serververwaltung nicht implizit")

    def test_only_explicit_team_role_or_profile_grants(self):
        member = self.member([10])
        perms, _ = pulse_permissions.compute(self.guild_with(member), 5, {"team_role_ids": [10],
                                                                           "permissions": {"10": {"can_manage_server": True}}})
        self.assertTrue(perms["can_manage_server"])
        config = {"team_role_ids": [], "permissions": {"10": {"can_manage_server": True}},
                  "dashboard_roles": {"p": {"discord_role_ids": [10], "permissions": {"can_manage_server": True}}}}
        perms, _ = pulse_permissions.compute(self.guild_with(member), 5, config)
        self.assertFalse(perms["can_manage_server"], "Keine Teamrolle → kein Recht, auch nicht über Profil")

    def test_server_admin_has_it(self):
        member = SimpleNamespace(id=1, roles=[], guild_permissions=SimpleNamespace(administrator=False))
        perms, _ = pulse_permissions.compute(self.guild_with(member), 1, {})
        self.assertTrue(perms["is_admin"] and perms["can_manage_server"])

    def test_navigation_mirrors_permission(self):
        self.assertIn("server-control", [s[0] for s in admin_sections(ADMIN)])
        self.assertEqual([s[0] for s in admin_sections(SERVER_OPERATOR)], ["server-control"])
        self.assertNotIn("server-control", [s[0] for s in admin_sections(TEAMLER)])
        self.assertTrue(can_open_admin(SERVER_OPERATOR))
        self.assertIn("server-control", SERVER_SECTION_KEYS)

    def test_sidebar_has_server_group(self):
        html = ws.get_sidebar_html("Server", "server-control", {"id": "1"}, ADMIN)
        self.assertIn("Serververwaltung", html)
        self.assertIn('href="/admin/server"', html)
        html = ws.get_sidebar_html("Server", "server-control", {"id": "1"}, TEAMLER)
        self.assertNotIn("/admin/server", html)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.audit = []
        self.patches = [
            patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
            patch.object(ws, "BASE_DIR", Path(self.tmp.name)),
            patch.object(ws, "BACKUP_DIR", str(Path(self.tmp.name) / "backups")),
            patch.object(ws, "log_audit", lambda *a, **k: self.audit.append(a)),
            patch.dict(os.environ, {"PULSE_HOST_AGENT_URL": "", "PULSE_HOST_AGENT_TOKEN": "", "PULSE_GITHUB_REPO": ""}),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        ws._rate_hits.clear()
        self.client = TestClient(ws.app)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def as_user(self, perms):
        return patch.object(ws, "auth", fake_auth_for(perms))

    def call(self, method, url, body=None, **kw):
        if method == "post":
            return self.client.post(url, json=body if body is not None else {}, **kw)
        return self.client.get(url, **kw)

    def test_teamler_gets_403_everywhere_including_direct_api_calls(self):
        with self.as_user(TEAMLER):
            for method, url, body in ROUTES:
                with self.subTest(url=url):
                    self.assertEqual(self.call(method, url, body).status_code, 403)

    def test_health_api_reports_live_values_for_operator(self):
        with self.as_user(SERVER_OPERATOR):
            page = self.client.get("/admin/server")
            self.assertIn('data-tab="health"', page.text)
            r = self.client.get("/admin/server/api/health")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertTrue(data["ok"])
        self.assertIn(data["status"], {"ok", "warn", "bad"})
        for key in ("memory", "scheduler", "discord", "connections", "databases", "checks"):
            self.assertIn(key, data)
        self.assertIn("pulse.db", [f["name"] for f in data["databases"]["files"]])
        self.assertEqual(self.audit, [])  # read-only: nothing audited, nothing changed

    def test_operator_without_admin_can_open_page_but_not_restore_links(self):
        with self.as_user(SERVER_OPERATOR):
            page = self.client.get("/admin/server")
            self.assertEqual(page.status_code, 200)
            self.assertIn("Server Control Center", page.text)
            self.assertIn("Host-Verwaltungsdienst nicht eingerichtet", page.text)
            self.assertIn("const IS_ADMIN=false", page.text)
            backups = self.client.get("/admin/server/api/backups").json()
            self.assertFalse(backups["can_restore"])

    def test_actions_require_json_confirmation_and_valid_parameters(self):
        with self.as_user(ADMIN):
            r = self.client.post("/admin/server/api/actions", data={"action": "restart", "confirm": "NEUSTART"})
            self.assertEqual(r.status_code, 415, "Formular-Requests (CSRF) werden abgelehnt")
            self.assertEqual(self.call("post", "/admin/server/api/actions", {"action": "rm -rf /"}).status_code, 400)
            self.assertEqual(self.call("post", "/admin/server/api/actions", {"action": "restart"}).status_code, 400)
            self.assertEqual(self.call("post", "/admin/server/api/actions", {"action": "restart", "confirm": "ja"}).status_code, 400)
            self.assertEqual(self.call("post", "/admin/server/api/actions", {"action": "deploy", "confirm": "DEPLOY",
                                                                            "expected_sha": "main; reboot"}).status_code, 400)
            self.assertEqual(self.call("post", "/admin/server/api/actions", {"action": "rollback", "confirm": "ROLLBACK",
                                                                            "target_sha": "HEAD~1"}).status_code, 400)
            self.assertEqual(self.call("get", "/admin/server/api/jobs/..%2F..%2Fetc").status_code, 404)
            self.assertEqual(self.call("get", "/admin/server/api/jobs/XYZ").status_code, 400)
            self.assertEqual(self.call("get", "/admin/server/api/logs/stream?source=bash").status_code, 400)
            self.assertEqual(self.call("get", "/admin/server/api/logs/stream?since=$(id)").status_code, 400)

    def test_missing_host_agent_never_reports_success(self):
        with self.as_user(ADMIN):
            for action, extra in (("restart", {"confirm": "NEUSTART"}), ("deploy", {"confirm": "DEPLOY", "expected_sha": SHA_NEW}),
                                  ("rollback", {"confirm": "ROLLBACK", "target_sha": SHA_OLDER}), ("healthcheck", {})):
                ws._rate_hits.clear()
                r = self.call("post", "/admin/server/api/actions", {"action": action, **extra})
                self.assertEqual(r.status_code, 503, action)
                self.assertFalse(r.json()["ok"])
                self.assertEqual(r.json()["reason"], "not_configured")
            deploy = self.client.get("/admin/server/api/deploy").json()
            self.assertFalse(deploy["git"]["available"])
            overview = self.client.get("/admin/server/api/overview").json()
            self.assertFalse(overview["agent"]["available"])
            self.assertFalse(overview["compare"]["github"]["available"])
            self.assertIsNone(overview["compare"]["up_to_date"])
        self.assertTrue(any("nicht gestartet" in a[3] for a in self.audit), "Ablehnung steht im Audit-Log")

    def test_console_allowlist_and_confirmation_for_actions(self):
        with self.as_user(ADMIN):
            for bad in ("whoami; cat .env", "logs 5; rm -rf /", "help me", "restart now", "version 3", "bash", "", "x" * 30,
                        "../../etc/passwd", "sync && reboot"):
                with self.subTest(command=bad):
                    r = self.call("post", "/admin/server/api/console", {"command": bad})
                    self.assertEqual(r.status_code, 400)
                    self.assertIn("Shell", r.json()["output"])
            r = self.call("post", "/admin/server/api/console", {"command": "help"}).json()
            self.assertTrue(r["ok"])
            for name in ("status", "health", "logs", "ram", "disk", "version", "backup", "update", "restart"):
                self.assertIn(name, r["output"])
            restart = self.call("post", "/admin/server/api/console", {"command": "restart"}).json()
            self.assertTrue(restart["requires_confirmation"])
            self.assertEqual(restart["action"], "restart")
            self.assertEqual(self.call("post", "/admin/server/api/console", {"command": "update"}).json()["action"], "deploy")
            for cmd in ("ram", "disk", "version", "status", "logs 20"):
                self.assertEqual(self.call("post", "/admin/server/api/console", {"command": cmd}).status_code, 200, cmd)
            ram = self.call("post", "/admin/server/api/console", {"command": "ram"}).json()["output"]
            self.assertIn("Host: nicht verfügbar", ram)
        self.assertTrue(any("Konsole abgelehnt" in a[2] for a in self.audit))

    def test_console_and_stream_output_is_redacted(self):
        import logging
        logging.getLogger("scc_test").warning("Login mit DISCORD_TOKEN=%s und Bot %s", DISCORD_LIKE, DISCORD_LIKE)
        with self.as_user(ADMIN), patch.dict(os.environ, {"DISCORD_TOKEN": DISCORD_LIKE}):
            out = self.call("post", "/admin/server/api/console", {"command": "logs 200"}).json()["output"]
            self.assertNotIn(DISCORD_LIKE, out)
            self.assertIn("ENTFERNT", out)

    def test_parallel_and_double_click_protection_for_local_backup(self):
        started = threading.Event()
        release = threading.Event()

        real_create = pulse_backup.create_full_backup

        def slow_backup(base_dir, backup_dir, **kwargs):
            started.set()
            release.wait(5)
            return real_create(base_dir, backup_dir, **kwargs)

        with self.as_user(ADMIN), patch.object(pulse_backup, "create_full_backup", slow_backup), \
                patch.object(ws, "build_discord_structure", lambda guild: {"roles": []}):
            first = self.call("post", "/admin/server/api/actions", {"action": "backup"})
            self.assertEqual(first.status_code, 202)
            second = self.call("post", "/admin/server/api/actions", {"action": "backup"})
            self.assertEqual(second.status_code, 409, "Doppelklick")
            ws._rate_hits.clear()
            third = self.call("post", "/admin/server/api/actions", {"action": "backup"})
            self.assertEqual(third.status_code, 409, "paralleler Vorgang")
            release.set()
            job_id = first.json()["job"]["id"]
            for _ in range(200):
                job = self.client.get(f"/admin/server/api/jobs/{job_id}").json()["job"]
                if job["status"] != "running":
                    break
                time.sleep(0.02)
            self.assertEqual(job["status"], "succeeded")
            self.assertIn("verifiziert", job["result"]["message"])

    def test_sse_stream_sends_lines_heartbeat_and_ends_after_limit(self):
        import logging
        logging.getLogger("scc_stream").error("stream-marker-%s", "xyz")
        with self.as_user(ADMIN), patch.object(scc, "STREAM_MAX_SECONDS", 0.3), patch.object(scc, "STREAM_POLL_SECONDS", 0.05):
            with self.client.stream("GET", "/admin/server/api/logs/stream?source=app") as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/event-stream", response.headers["content-type"])
                text = "".join(response.iter_text())
        self.assertIn("event: status", text)
        self.assertIn('"state": "connected"', text)
        self.assertIn("stream-marker-xyz", text)
        self.assertIn('"level": "error"', text)
        self.assertIn('"state": "expired"', text)

    def test_sse_stream_reports_unreachable_agent_and_limits_streams(self):
        env = {"PULSE_HOST_AGENT_URL": "http://127.0.0.1:9", "PULSE_HOST_AGENT_TOKEN": TOKEN}
        with self.as_user(ADMIN), patch.dict(os.environ, env), patch.object(scc, "STREAM_MAX_SECONDS", 0.4), \
                patch.object(scc, "STREAM_POLL_SECONDS", 0.05):
            with self.client.stream("GET", "/admin/server/api/logs/stream?source=container") as response:
                text = "".join(response.iter_text())
            self.assertIn("event: source_error", text)
            self.assertIn("nicht erreichbar", text)
            self.assertNotIn(TOKEN, text)
            with patch.object(scc, "STREAM_MAX_PER_USER", 0):
                self.assertEqual(self.client.get("/admin/server/api/logs/stream?source=app").status_code, 429)

    def test_responses_never_contain_tokens(self):
        env = {"PULSE_HOST_AGENT_URL": "http://127.0.0.1:9", "PULSE_HOST_AGENT_TOKEN": TOKEN}
        with self.as_user(ADMIN), patch.dict(os.environ, env), patch.object(scc, "STREAM_MAX_SECONDS", 0.2):
            for method, url, body in ROUTES:
                ws._rate_hits.clear()
                r = self.call(method, url, body)
                self.assertNotIn(TOKEN, r.text, url)

    def test_backup_delete_refuses_only_verified_backup(self):
        backup_dir = Path(ws.full_backup_dir())
        (Path(self.tmp.name) / "config.json").write_text("{}", encoding="utf-8")
        good, _ = pulse_backup.create_full_backup(self.tmp.name, backup_dir)
        with self.as_user(ADMIN):
            r = self.client.post("/backup/full/delete", data={"filename": good.name}, follow_redirects=False)
            self.assertEqual(r.status_code, 303)
            from urllib.parse import unquote
            self.assertIn("einzige", unquote(r.headers["location"]))
            self.assertTrue(good.exists())
            time.sleep(0.01)
            second, _ = pulse_backup.create_full_backup(self.tmp.name, backup_dir)
            r = self.client.post("/backup/full/delete", data={"filename": good.name}, follow_redirects=False)
            self.assertFalse(good.exists(), "mit einer zweiten geprüften Sicherung darf gelöscht werden")
            self.assertTrue(second.exists())


# ----------------------------------------------------------------------------- helpers
class HelperTests(unittest.TestCase):
    def test_redact(self):
        for text in (f"token {DISCORD_LIKE}", "DISCORD_CLIENT_SECRET=abc123xyz", "PULSE_HOST_AGENT_TOKEN: geheim12345",
                     "Authorization: Bearer abcdefghijklmnopqrstuvwxyz123", "https://user:pass@example.com/repo",
                     "https://discord.com/api/webhooks/1/abc", "-----BEGIN OPENSSH PRIVATE KEY-----"):
            for fn in (scc.redact, agent_mod.redact):
                out = fn(text)
                self.assertTrue("ENTFERNT" in out, (fn.__module__, text, out))
                self.assertNotIn("geheim12345", out)
                self.assertNotIn("abc123xyz", out)

    def test_agent_url_must_be_private(self):
        with patch.dict(os.environ, {"PULSE_HOST_AGENT_URL": "http://8.8.8.8:25096", "PULSE_HOST_AGENT_TOKEN": TOKEN}):
            self.assertFalse(scc.agent_config()["configured"])
        with patch.dict(os.environ, {"PULSE_HOST_AGENT_URL": "https://example.com", "PULSE_HOST_AGENT_TOKEN": TOKEN}):
            self.assertFalse(scc.agent_config()["configured"])
        with patch.dict(os.environ, {"PULSE_HOST_AGENT_URL": "http://172.30.0.1:25096", "PULSE_HOST_AGENT_TOKEN": "kurz"}):
            self.assertFalse(scc.agent_config()["configured"])
        with patch.dict(os.environ, {"PULSE_HOST_AGENT_URL": "unix:///app/.pulse-agent/agent.sock", "PULSE_HOST_AGENT_TOKEN": TOKEN}):
            cfg = scc.agent_config()
            self.assertTrue(cfg["configured"])
            self.assertEqual(cfg["socket"], "/app/.pulse-agent/agent.sock")
        with patch.dict(os.environ, {"PULSE_HOST_AGENT_URL": "unix://relative/../x.sock", "PULSE_HOST_AGENT_TOKEN": TOKEN}):
            self.assertFalse(scc.agent_config()["configured"])

    def test_parse_command(self):
        self.assertEqual(scc.parse_command("LOGS 50"), ("logs", 50))
        self.assertEqual(scc.parse_command(" help "), ("help", None))
        for bad in ("logs -1", "logs 9999", "ram 3", "rm", "help;id", "status\nid"):
            self.assertIsNone(scc.parse_command(bad), bad)

    def test_classify(self):
        self.assertEqual(scc.classify("Traceback (most recent call last)"), "error")
        self.assertEqual(scc.classify("⚠️ Event-Loop verzögert"), "warning")
        self.assertEqual(scc.classify("Bot gestartet"), "info")
        self.assertEqual(scc.classify("x", "ERROR"), "error")

    def test_ts_to_epoch(self):
        self.assertAlmostEqual(scc._ts_to_epoch("2026-10-23T10:00:00.123456789Z"), 1792749600.123456, places=5)
        self.assertIsNone(scc._ts_to_epoch("kaputt"))

    def test_read_git_head(self):
        with tempfile.TemporaryDirectory() as tmp:
            git = Path(tmp) / ".git"
            (git / "refs" / "heads").mkdir(parents=True)
            (git / "HEAD").write_text("ref: refs/heads/main\n")
            (git / "packed-refs").write_text(f"# pack-refs\n{SHA_NEW} refs/heads/main\n")
            self.assertEqual(scc.read_git_head(tmp), {"sha": SHA_NEW, "short": SHA_NEW[:7], "branch": "main"})
            (git / "HEAD").write_text("ref: ../../etc/passwd\n")
            self.assertEqual(scc.read_git_head(tmp), {})

    def test_live_console_buffer_is_bounded_and_structured(self):
        import logging
        for i in range(pulse_live_console.MAX_RECORDS + 20):
            logging.getLogger("scc_bound").warning("bound-%s", i)
        records = pulse_live_console.records_since(0, 10_000)
        self.assertLessEqual(len(records), pulse_live_console.MAX_RECORDS)
        last = records[-1]["seq"]
        self.assertEqual(pulse_live_console.records_since(last), [])
        logging.getLogger("pulse.scc").error("pulse-logger-marker")
        self.assertTrue(any("pulse-logger-marker" in r["text"] for r in pulse_live_console.records_since(last)),
                        "nicht propagierender pulse-Logger wird erfasst")

    def test_diagnose_marks_confirmed_vs_assumption(self):
        facts = {"bot_ready": True, "guild_ready": True, "database_ok": True, "gateway_latency_ms": 2500,
                 "memory": {"container_mb": 430, "limit_mb": 512, "warn_mb": 350, "critical_mb": 420, "emergency_mb": 470},
                 "disk": {"free_gb": 0.5}, "jobs_failing": [], "backup": {"count": 0, "restore_test": {}}}
        findings = scc.diagnose(facts, {"available": False, "reason": "not_configured", "message": "nicht eingerichtet"}, None, None,
                                [], [{"text": "HTTP 429 Too Many Requests", "time": "t"}])
        by = {f["component"]: f for f in findings}
        self.assertTrue(by["Arbeitsspeicher"]["confirmed"])
        self.assertFalse(by["Discord-Gateway"]["confirmed"])
        self.assertFalse(by["Externe Dienste"]["confirmed"])
        self.assertIn("Backups", by)
        for f in findings:
            self.assertTrue(f["at"] and f["recommendation"] and f["cause"])
        self.assertEqual(findings[0]["severity"], "bad")

    def test_backup_job_reports_success_only_after_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "config.json").write_text('{"a": 1}', encoding="utf-8")

            def corrupt():
                path, manifest = pulse_backup.create_full_backup(tmp, Path(tmp) / "backups")
                with zipfile.ZipFile(path) as zf:
                    items = {n: zf.read(n) for n in zf.namelist()}
                items["json/config.json"] = b'{"a": 2}'
                with zipfile.ZipFile(path, "w") as zf:
                    for n, data in items.items():
                        zf.writestr(n, data)
                return path, manifest

            job = scc._new_local_job("backup", "Tester")
            scc.run_backup_job(job, corrupt)
            self.assertEqual(job["status"], "failed")
            self.assertIn("Verifikation", job["result"]["message"])

            def good():
                return pulse_backup.create_full_backup(tmp, Path(tmp) / "backups")

            job = scc._new_local_job("backup", "Tester")
            scc.run_backup_job(job, good)
            self.assertEqual(job["status"], "succeeded")

    def test_backup_name_pattern_lists_deploy_backups(self):
        for name in ("pulse_full_predeploy_20261023_101010_000001.zip", "pulse_full_prerollback_20261023_101010_000001.zip"):
            self.assertTrue(pulse_backup.NAME_RE.fullmatch(name))
            self.assertTrue(scc.BACKUP_NAME_RE.fullmatch(name))
        self.assertEqual(scc.backup_kind("pulse_full_predeploy_20261023_101010.zip"), "vor Deploy")
        self.assertIsNone(pulse_backup.NAME_RE.fullmatch("pulse_full_../../x_20261023_101010.zip"))


# ----------------------------------------------------------------------------- no shell anywhere
class NoShellTests(unittest.TestCase):
    def test_sources_never_use_a_shell(self):
        for rel in ("pulse_server_control.py", "deploy/pulse_host_agent.py", "pulse_live_console.py"):
            source = (ROOT / rel).read_text(encoding="utf-8")
            code = re.sub(r'""".*?"""', "", source, flags=re.S)
            for forbidden in ("shell=True", "os.system(", "os.popen(", "eval(", "exec(", "bash -c", "/bin/sh"):
                self.assertNotIn(forbidden, code, f"{rel}: {forbidden}")
        agent_source = (ROOT / "deploy/pulse_host_agent.py").read_text(encoding="utf-8")
        self.assertIn("shell=False", agent_source)
        self.assertNotIn("docker.sock", (ROOT / "compose.yaml").read_text(encoding="utf-8"), "kein Docker-Socket im Webcontainer")

    def test_runner_rejects_string_commands(self):
        with self.assertRaises(TypeError):
            agent_mod.Runner().run("git status; id")

    def test_agent_config_refuses_public_bind_and_short_token(self):
        base = {"PULSE_AGENT_TOKEN": TOKEN}
        for bind in ("0.0.0.0", "8.8.8.8", "::", "localhost"):
            with self.assertRaises(agent_mod.ConfigError, msg=bind):
                agent_mod.Config(dict(base, PULSE_AGENT_BIND=bind))
        with self.assertRaises(agent_mod.ConfigError):
            agent_mod.Config({"PULSE_AGENT_TOKEN": "kurz"})
        with self.assertRaises(agent_mod.ConfigError):
            agent_mod.Config(dict(base, PULSE_HEALTH_URL="http://example.com/healthz"))
        with self.assertRaises(agent_mod.ConfigError):
            agent_mod.Config(dict(base, PULSE_CONTAINER="pulse; rm -rf /"))
        with self.assertRaises(agent_mod.ConfigError):
            agent_mod.Config(dict(base, PULSE_AGENT_SOCKET="relative.sock"))


# ----------------------------------------------------------------------------- host agent behaviour
class HostAgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_deploy_aborts_on_local_changes_without_running_the_script(self):
        runner = FakeRunner(status=" M webserver.py\n?? assets/support_wait.mp3\n")
        agent = make_agent(self.tmp.name, runner)
        job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW})["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("unsicherem Git-Zustand", job["result"]["message"])
        self.assertEqual(runner.streams, [], "remote_deploy.sh darf nicht starten")

    def test_allowed_untracked_wait_music_is_not_a_blocker(self):
        info = make_agent(self.tmp.name, FakeRunner(status="?? assets/support_wait.mp3\n")).git_info()
        self.assertTrue(info["safe"], info["reasons"])

    def test_deploy_aborts_on_wrong_branch_unfinished_merge_and_non_fast_forward(self):
        for runner, marker in ((FakeRunner(branch="feature/x"), "Branch"), (FakeRunner(ancestor=False), "Fast-Forward")):
            agent = make_agent(self.tmp.name, runner)
            job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW})["id"])
            self.assertEqual(job["status"], "failed")
            self.assertIn(marker, job["result"]["message"])
            self.assertEqual(runner.streams, [])
        (Path(self.tmp.name) / ".git" / "MERGE_HEAD").write_text("x")
        agent = make_agent(self.tmp.name, FakeRunner())
        job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW})["id"])
        self.assertIn("Merge", job["result"]["message"])

    def test_deploy_aborts_when_github_changed_since_preview_or_unreachable(self):
        agent = make_agent(self.tmp.name, FakeRunner(origin=SHA_OLDER))
        job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW})["id"])
        self.assertIn("seit der Vorschau geändert", job["result"]["message"])
        agent = make_agent(self.tmp.name, FakeRunner(fetch_ok=False))
        job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW})["id"])
        self.assertIn("GitHub nicht erreichbar", job["result"]["message"])

    def test_successful_deploy_requires_installed_commit_and_repeated_health(self):
        runner = FakeRunner(stream_lines=["Backup vor dem Deploy erstellt.", "Deploy erfolgreich: x ist gesund."], after_stream_head=SHA_NEW)
        agent = make_agent(self.tmp.name, runner)
        job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW}, actor="Tester")["id"])
        self.assertEqual(job["status"], "succeeded", job["result"])
        self.assertEqual(job["result"]["installed_commit"], SHA_NEW)
        self.assertEqual(runner.streams[0][0], "bash")
        self.assertTrue(runner.streams[0][1].endswith("remote_deploy.sh"))
        self.assertEqual(runner.streams[0][2], SHA_NEW)
        self.assertEqual(agent.state["last_code_change"]["previous"], SHA_OLD)

    def test_script_success_but_wrong_commit_or_unhealthy_is_a_failure(self):
        agent = make_agent(self.tmp.name, FakeRunner(stream_lines=["Backup vor dem Deploy erstellt."]))
        job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW})["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("statt", job["result"]["message"])
        agent = make_agent(self.tmp.name, FakeRunner(stream_lines=["Backup vor dem Deploy erstellt."], after_stream_head=SHA_NEW),
                           healthy=False)
        job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW})["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("Healthcheck", job["result"]["message"])

    def test_build_failure_with_rollback_results_are_reported_transparently(self):
        cases = (("Rollback erfolgreich: x läuft wieder.", True, "automatischer Rollback"),
                 ("::error::Auch der Rollback ist nicht gesund – bitte sofort prüfen", False, "NICHT gesund"))
        for line, ok, text in cases:
            runner = FakeRunner(stream_lines=["Backup vor dem Deploy erstellt.", "::error::/healthz nicht gesund – Rollback auf x.", line],
                                stream_code=1)
            agent = make_agent(self.tmp.name, runner)
            job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW})["id"])
            self.assertEqual(job["status"], "failed")
            self.assertEqual(job["result"]["rollback"]["ok"], ok)
            self.assertIn(text, job["result"]["message"])

    def test_backup_failure_before_any_change(self):
        agent = make_agent(self.tmp.name, FakeRunner(stream_lines=["Traceback …"], stream_code=1))
        job = wait_done(agent, agent.start_job("deploy", {"expected_sha": SHA_NEW})["id"])
        self.assertIn("vor jeder Änderung", job["result"]["message"])

    def test_only_one_job_at_a_time_and_cancel_rules(self):
        gate = threading.Event()
        runner = FakeRunner(stream_lines=[lambda: gate.wait(5), "Backup vor dem Deploy erstellt.", lambda: gate.wait(5)],
                            after_stream_head=SHA_NEW)
        agent = make_agent(self.tmp.name, runner)
        first = agent.start_job("deploy", {"expected_sha": SHA_NEW})
        with self.assertRaises(RuntimeError):
            agent.start_job("restart", {})
        for _ in range(100):
            if runner.streams:
                break
            time.sleep(0.01)
        self.assertTrue(agent.public_job(agent.find_job(first["id"]))["cancellable"])
        gate.set()
        job = wait_done(agent, first["id"])
        self.assertEqual(job["status"], "succeeded")
        with self.assertRaises(RuntimeError):
            agent.cancel_job(first["id"])

    def test_cancel_after_point_of_no_return_is_refused(self):
        gate, reached = threading.Event(), threading.Event()
        runner = FakeRunner(stream_lines=["Backup vor dem Deploy erstellt.", reached.set, lambda: gate.wait(5)], after_stream_head=SHA_NEW)
        agent = make_agent(self.tmp.name, runner)
        job = agent.start_job("deploy", {"expected_sha": SHA_NEW})
        self.assertTrue(reached.wait(5))
        with self.assertRaises(RuntimeError):
            agent.cancel_job(job["id"])
        gate.set()
        wait_done(agent, job["id"])

    def test_rollback_validates_target_and_reports_failures(self):
        agent = make_agent(self.tmp.name, FakeRunner())
        with self.assertRaises(ValueError):
            agent.start_job("rollback", {"target_sha": "d" * 40})
        with self.assertRaises(ValueError):
            agent.start_job("rollback", {"target_sha": "HEAD~1"})
        runner = FakeRunner(origin=None, stream_lines=["Backup vor dem Rollback erstellt.", "::error::KRITISCH: Auch der vorherige Stand ist nicht gesund"],
                            stream_code=1)
        agent = make_agent(self.tmp.name, runner)
        job = wait_done(agent, agent.start_job("rollback", {"target_sha": SHA_OLDER})["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("ebenfalls nicht gesund", job["result"]["message"])
        self.assertTrue(runner.streams[0][1].endswith("rollback_code.sh"))
        runner = FakeRunner(stream_lines=["Backup vor dem Rollback erstellt.", "Rollback erfolgreich"], after_stream_head=SHA_OLDER)
        agent = make_agent(self.tmp.name, runner, health_sequence=[False, True, True, True])
        job = wait_done(agent, agent.start_job("rollback", {"target_sha": SHA_OLDER})["id"])
        self.assertEqual(job["status"], "succeeded", job["result"])
        self.assertIn("Boot-Update", job["result"]["message"])

    def test_restart_detects_failed_health(self):
        agent = make_agent(self.tmp.name, FakeRunner(), healthy=False)
        job = wait_done(agent, agent.start_job("restart", {})["id"])
        self.assertEqual(job["status"], "failed")
        self.assertIn("Healthcheck", job["result"]["message"])
        self.assertFalse(job["cancellable"])

    def test_status_and_logs_are_redacted_and_bounded(self):
        agent = make_agent(self.tmp.name, FakeRunner())
        status = agent.status()
        self.assertNotIn(DISCORD_LIKE, json.dumps(status))
        self.assertEqual(status["container"]["status"], "running")
        logs = agent.logs(5)
        self.assertNotIn(DISCORD_LIKE, json.dumps(logs))
        with self.assertRaises(ValueError):
            agent.logs("5; id")
        with self.assertRaises(ValueError):
            agent.logs(10, since="$(id)")

    def test_http_layer_requires_token_and_rejects_unknown(self):
        agent = make_agent(self.tmp.name, FakeRunner())
        server = agent_mod.ThreadingHTTPServer(("127.0.0.1", 0), agent_mod.make_handler(agent))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        import urllib.error
        import urllib.request
        base = f"http://127.0.0.1:{server.server_address[1]}"

        def call(path, token=None, method="GET", body=None, ctype="application/json"):
            data = json.dumps(body).encode() if body is not None else None
            req = urllib.request.Request(base + path, data=data, method=method)
            if token:
                req.add_header("Authorization", "Bearer " + token)
            if data is not None:
                req.add_header("Content-Type", ctype)
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    return r.status, json.loads(r.read())
            except urllib.error.HTTPError as e:
                return e.code, json.loads(e.read() or b"{}")

        try:
            self.assertEqual(call("/v1/ping")[0], 401)
            self.assertEqual(call("/v1/ping", "falsch" * 8)[0], 401)
            self.assertEqual(call("/v1/ping", TOKEN)[0], 200)
            self.assertEqual(call("/v1/exec", TOKEN)[0], 404)
            self.assertEqual(call("/v1/jobs", TOKEN, "POST", {"action": "shell", "cmd": "id"})[0], 400)
            self.assertEqual(call("/v1/jobs", TOKEN, "POST", {"action": "deploy", "expected_sha": "main"})[0], 400)
            self.assertEqual(call("/v1/jobs", TOKEN, "POST", {"action": "restart"}, ctype="text/plain")[0], 400)
            self.assertEqual(call("/v1/logs?tail=abc", TOKEN)[0], 400)
            self.assertEqual(call("/v1/jobs/zzzz/cancel", TOKEN, "POST", {})[0], 404)
            status, data = call("/v1/status", TOKEN)
            self.assertEqual(status, 200)
            self.assertNotIn(TOKEN, json.dumps(data))
        finally:
            server.shutdown()
            server.server_close()


class DeployScriptTests(unittest.TestCase):
    def test_rollback_script_matches_deploy_safety_rules(self):
        script = (ROOT / "deploy" / "rollback_code.sh").read_text(encoding="utf-8")
        self.assertNotIn("\r\n", script)
        for needle in ("set -euo pipefail", "^[0-9a-f]{40}$", "merge-base --is-ancestor", "git diff --quiet", "verify_full_backup",
                       'prefix="prerollback_"', "Backup vor dem Rollback erstellt.", 'git reset --hard "$PREV"'):
            self.assertIn(needle, script)
        self.assertLess(script.index("verify_full_backup"), script.index('git reset --hard "$SHA"'), "Backup vor Codeänderung")
        self.assertIn("Backup vor dem Deploy erstellt.", (ROOT / "deploy" / "remote_deploy.sh").read_text(encoding="utf-8"),
                      "Marker, auf den der Host-Dienst den Abbruchschutz stützt")

    def test_systemd_unit_is_hardened_and_not_root(self):
        unit = (ROOT / "systemd" / "pulse-host-agent.service").read_text(encoding="utf-8")
        for needle in ("NoNewPrivileges=yes", "ProtectSystem=strict", "User=pulse", "EnvironmentFile=/etc/pulse-host-agent.env"):
            self.assertIn(needle, unit)
        self.assertNotIn("User=root", unit)

    def test_agent_socket_folder_is_git_ignored(self):
        self.assertIn(".pulse-agent/", (ROOT / ".gitignore").read_text(encoding="utf-8"))
        example = (ROOT / "deploy" / "pulse-host-agent.env.example").read_text(encoding="utf-8")
        self.assertRegex(example, r"PULSE_AGENT_TOKEN=HIER_")


if __name__ == "__main__":
    unittest.main()
