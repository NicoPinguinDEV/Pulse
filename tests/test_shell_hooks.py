"""A-01a: page head, sidebar and audit go through explicit shell hooks in webserver.

Before Phase 14.1 `pulse_pro.make_shell_helpers` replaced `webserver.get_head_html`, `get_sidebar_html`
and `log_audit` at runtime; every further call stacked another wrapper (the Pro head twice, every audit
event twice in the event table). Now webserver keeps its own functions and calls one registered hook
per slot (`webserver.register_shell_hook`).

The HTML snapshot (tests/shell_html_snapshot.json) was taken from the last version with the runtime
patches; the output must stay byte-identical. After an intended change to head or sidebar markup:

    python -m tests.test_shell_hooks --update

and review the diff of tests/shell_html_snapshot.json.
"""
import ast
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_version
import webserver as ws
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (Phase 9: no ./pulse.db)

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = Path(__file__).with_name("shell_html_snapshot.json")
HOOKED = ("get_head_html", "get_sidebar_html", "log_audit")

ADMIN = dict({k: True for k in ws.PERM_KEYS}, is_admin=True)
TEAMLER = dict({k: False for k in ws.PERM_KEYS}, can_view_dashboard=True, can_manage_tickets=True, is_admin=False)
LEADER = dict(TEAMLER, can_promote=True, can_warn=True, can_view_analytics=True, can_manage_factions=True)
USER = {"id": "123456789012345678", "global_name": "Nico <b>", "avatar": "abc123"}

HEAD_CASES = {
    "dashboard": "Übersicht - Bochum RP",
    "login": "Bochum RP Panel Login",
    "escaped": '<script>alert("x")</script> & Co',
    "empty": "",
}
SIDEBAR_CASES = {
    "guest": ("Bochum RP", "dashboard", None, None, 0),
    "teamler_me": ("Bochum RP", "me", USER, TEAMLER, 0),
    "teamler_inbox_unread": ("Bochum RP", "inbox", USER, TEAMLER, 7),
    "leader_factions": ("Bochum <RP>", "factions", USER, LEADER, 0),
    "leader_advanced": ("Bochum RP", "u:calendar", USER, LEADER, 0),
    "admin_dashboard": ("Bochum RP", "dashboard", USER, ADMIN, 2),
    "admin_area": ("Bochum RP", "admin", USER, ADMIN, 0),
    "admin_server_area": ("Bochum RP", "server-control", USER, ADMIN, 0),
}


def _digest(html: str) -> dict:
    normalized = html.replace(pulse_version.PULSE_VERSION, "{VERSION}")  # version bumps are no markup change
    return {"sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(), "length": len(normalized)}


def render_cases() -> dict:
    out = {"head": {}, "sidebar": {}}
    for name, title in HEAD_CASES.items():
        out["head"][name] = _digest(ws.get_head_html(title))
    for name, (guild, page, user, perms, unread) in SIDEBAR_CASES.items():
        with patch.object(pulse_db, "unread_count", return_value=unread):
            out["sidebar"][name] = _digest(ws.get_sidebar_html(guild, page, user, perms))
    return out


class ShellSnapshotTests(unittest.TestCase):
    """The registered hooks render exactly what the former runtime patches rendered."""

    @classmethod
    def setUpClass(cls):
        cls.expected = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        cls.actual = render_cases()

    def test_head_snapshot(self):
        self.assertEqual(set(self.expected["head"]), set(HEAD_CASES))
        for name in HEAD_CASES:
            with self.subTest(case=name):
                self.assertEqual(self.actual["head"][name], self.expected["head"][name])

    def test_sidebar_snapshot(self):
        self.assertEqual(set(self.expected["sidebar"]), set(SIDEBAR_CASES))
        for name in SIDEBAR_CASES:
            with self.subTest(case=name):
                self.assertEqual(self.actual["sidebar"][name], self.expected["sidebar"][name])


class ShellOutputTests(unittest.TestCase):
    def test_head_is_base_head_plus_one_pro_extension(self):
        import pulse_pro
        html = ws.get_head_html("<Titel>")
        self.assertTrue(html.startswith(ws._base_head_html("<Titel>")))
        self.assertTrue(html.endswith(pulse_pro.pro_head_extra("<Titel>")))
        self.assertIn("<title>&lt;Titel&gt;</title>", html)
        for marker in ('rel="manifest"', 'name="theme-color"', "pulseParticles"):
            with self.subTest(marker=marker):
                self.assertIn(marker, html)
        self.assertEqual(html.count('<link rel="manifest"'), 1)
        self.assertEqual(html.count("<title>"), 1)

    def test_sidebar_is_the_pro_navigation_with_permission_filtering(self):
        with patch.object(pulse_db, "unread_count", return_value=4):
            teamler = ws.get_sidebar_html("G", "inbox", USER, TEAMLER)
            admin = ws.get_sidebar_html("G", "admin", USER, ADMIN)
        self.assertIn('id="pulseSidebar"', teamler)
        self.assertNotIn('id="sidebar"', teamler)  # not the built-in fallback
        self.assertIn(">4</span>", teamler)
        self.assertNotIn('href="/factions"', teamler)
        self.assertNotIn('href="/admin"', teamler)
        self.assertIn("Nico &lt;b&gt;", teamler)
        self.assertIn('href="/admin"', admin)
        self.assertIn("Serververwaltung", admin)

    def test_webserver_render_page_uses_the_registered_shell(self):
        ctx = type("Ctx", (), {"guild_name": "G", "user": USER, "perms": TEAMLER, "msg": "", "ok": True})()
        with patch.object(pulse_db, "unread_count", return_value=0):
            html = ws.render_page("Titel", ctx, "me", "<p>body</p>")
        self.assertIn('id="pulseSidebar"', html)
        self.assertEqual(html.count('<link rel="manifest"'), 1)


class AuditHookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.audit_file = str(Path(self.tmp.name) / "audit_logs.json")
        self.events = []
        for p in (patch.object(ws, "AUDIT_FILE", self.audit_file),
                  patch.object(pulse_db, "record_event", lambda *a, **k: self.events.append((a, k)))):
            p.start()
            self.addCleanup(p.stop)

    def entries(self):
        return ws.load_json(self.audit_file, [])

    def test_one_audit_call_writes_one_entry_and_one_event(self):
        ws.log_audit("Nico", 42, "Test-Aktion", "Details")
        self.assertEqual([(e["actor"], e["actor_id"], e["action"], e["details"]) for e in self.entries()],
                         [("Nico", "42", "Test-Aktion", "Details")])
        self.assertEqual(self.events, [(("audit", "panel", "", 42, "Nico",
                                         {"action": "Test-Aktion", "details": "Details"}), {})])

    def test_system_actor_fallback_in_event(self):
        ws.log_audit("", "0", "Datenbereinigung", "x")
        self.assertEqual(self.events[0][0][4], "System")

    def test_repeated_registration_does_not_duplicate_events_or_head(self):
        import pulse_pro
        head_before = ws.get_head_html("T")
        pulse_pro.make_shell_helpers(ws)
        pulse_pro.make_shell_helpers(ws)
        ws.log_audit("Nico", "1", "A", "B")
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(len(self.events), 1)
        self.assertEqual(ws.get_head_html("T"), head_before)

    def test_failing_listener_keeps_the_saved_entry_and_the_caller_alive(self):
        def broken(*_a):
            raise RuntimeError("db weg")
        with patch.dict(ws._shell_hooks, {"audit_listener": broken}):
            ws.log_audit("Nico", "1", "A", "B")
        self.assertEqual(len(self.entries()), 1)

    def test_failing_event_table_keeps_the_saved_entry(self):
        with patch.object(pulse_db, "record_event", side_effect=RuntimeError("locked")):
            ws.log_audit("Nico", "1", "A", "B")
        self.assertEqual(len(self.entries()), 1)

    def test_without_listener_only_the_file_is_written(self):
        with patch.dict(ws._shell_hooks, clear=True):
            ws.log_audit("Nico", "1", "A", "B")
        self.assertEqual(len(self.entries()), 1)
        self.assertEqual(self.events, [])


class RegistryTests(unittest.TestCase):
    def test_pulse_pro_registers_all_three_slots(self):
        import pulse_pro
        self.assertEqual(set(ws.SHELL_HOOK_SLOTS), {"head_extra", "sidebar", "audit_listener"})
        self.assertIs(ws.shell_hook("head_extra"), pulse_pro.pro_head_extra)
        self.assertIs(ws.shell_hook("sidebar"), pulse_pro.pro_sidebar_html)
        self.assertIs(ws.shell_hook("audit_listener"), pulse_pro.pro_audit_mirror)

    def test_webserver_functions_are_not_replaced(self):
        for name in HOOKED:
            with self.subTest(function=name):
                fn = getattr(ws, name)
                self.assertEqual((fn.__module__, fn.__qualname__), ("webserver", name))

    def test_missing_hooks_fall_back_to_the_built_in_shell(self):
        with patch.dict(ws._shell_hooks, clear=True):
            self.assertIsNone(ws.shell_hook("sidebar"))
            self.assertEqual(ws.get_head_html("T"), ws._base_head_html("T"))
            with patch.object(pulse_db, "unread_count", return_value=0):
                html = ws.get_sidebar_html("G", "dashboard", USER, TEAMLER)
        self.assertIn('id="sidebar"', html)
        self.assertNotIn('id="pulseSidebar"', html)

    def test_same_hook_again_is_a_no_op(self):
        def hook(title):
            return "<!--x-->"
        with patch.dict(ws._shell_hooks, clear=True):
            ws.register_shell_hook("head_extra", hook)
            ws.register_shell_hook("head_extra", hook)
            self.assertEqual(ws.get_head_html("T").count("<!--x-->"), 1)

    def test_second_different_hook_is_refused_and_keeps_the_first(self):
        def first(title):
            return "<!--1-->"

        def second(title):
            return "<!--2-->"
        with patch.dict(ws._shell_hooks, clear=True):
            ws.register_shell_hook("head_extra", first)
            with self.assertRaisesRegex(RuntimeError, "bereits"):
                ws.register_shell_hook("head_extra", second)
            self.assertIs(ws.shell_hook("head_extra"), first)

    def test_unknown_slot_and_non_callable_are_refused(self):
        with patch.dict(ws._shell_hooks, clear=True):
            with self.assertRaises(ValueError):
                ws.register_shell_hook("compute_perms", lambda *a: None)
            with self.assertRaises(ValueError):
                ws.shell_hook("footer")
            with self.assertRaises(TypeError):
                ws.register_shell_hook("sidebar", "<aside>")
            self.assertEqual(ws._shell_hooks, {})

    def test_no_module_assigns_the_hooked_webserver_functions(self):
        """Static guard: no `ws.get_head_html = …` / `setattr(webserver, "log_audit", …)` outside tests."""
        offenders = []
        for path in [*ROOT.glob("*.py"), *ROOT.glob("cogs/*.py")]:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else []
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr in HOOKED:
                        offenders.append(f"{path.name}:{node.lineno}")
                if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "setattr" and len(node.args) >= 2
                        and isinstance(node.args[1], ast.Constant) and node.args[1].value in HOOKED):
                    offenders.append(f"{path.name}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__" and "--update" in sys.argv:
    setUpModule()
    try:
        SNAPSHOT.write_text(json.dumps(render_cases(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    finally:
        tearDownModule()
    print(f"geschrieben: {SNAPSHOT}")
elif __name__ == "__main__":
    unittest.main()
