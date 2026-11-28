"""F7: ticket events of the bot and the dashboard share one correlation ID (history, rights, no type change)."""
import ast
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_events as ev

ROOT = Path(__file__).resolve().parents[1]
TICKET = {"can_manage_tickets": True}
TASKER = {"can_manage_tasks": True}
ADMIN = {"is_admin": True}
NOBODY = {"can_view_dashboard": True}


def record_event_calls(path):
    if path == "webserver.py":
        sources = ("webserver.py", "pulse_web/ui.py", "pulse_web/dashboard.py", "pulse_web/tickets.py", "pulse_web/team.py", "pulse_web/applications.py", "pulse_web/backups.py", "pulse_web/administration.py")
        source = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in sources)
    else:
        source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name == "record_event" and node.args and isinstance(node.args[0], ast.Constant):
                yield node


class TicketCallSiteGuard(unittest.TestCase):
    """Every ticket event written by the bot or the dashboard carries ``correlation_id="ticket:<id>"``."""

    def check(self, path, minimum):
        sites = [n for n in record_event_calls(path) if str(n.args[0].value).startswith("ticket_")]
        self.assertGreaterEqual(len(sites), minimum, path)
        for node in sites:
            kw = {k.arg: k.value for k in node.keywords}
            self.assertIn("correlation_id", kw, f"{path}:{node.lineno} {node.args[0].value}")
            value = kw["correlation_id"]
            self.assertIsInstance(value, ast.JoinedStr, f"{path}:{node.lineno}")
            self.assertEqual(value.values[0].value, "ticket:", f"{path}:{node.lineno}")
            # the formatted part is the same expression as the event's target ID argument
            self.assertEqual(ast.dump(value.values[1].value), ast.dump(node.args[2]), f"{path}:{node.lineno}")

    def test_bot_ticket_events(self):
        self.check("cogs/tickets.py", 5)

    def test_dashboard_ticket_events(self):
        self.check("webserver.py", 5)


class SharedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.patch.start()
        pulse_db._SCHEMA_READY.discard(self.db_path)
        pulse_db.init_db()
        self.cid = ev.correlation_for("ticket", "ticket_ab12")
        # bot and dashboard write the legacy event types, exactly like the migrated call sites
        pulse_db.record_event("ticket_created", "ticket", "ticket_ab12", "7", "Anna", {"category": "x"}, correlation_id=self.cid)
        pulse_db.record_event("ticket_claimed_dashboard", "ticket", "ticket_ab12", "8", "Ben", {}, correlation_id=self.cid)
        pulse_db.record_event("ticket_closed", "ticket", "ticket_ab12", "8", "Ben", {"reason": "ok"}, correlation_id=self.cid)
        pulse_db.record_event("ticket_closed", "ticket", "ticket_zz99", "8", "Ben", {}, correlation_id="ticket:ticket_zz99")
        # a foreign-domain event that carries the same correlation ID must not leak to a ticket manager
        ev.emit("task", "created", correlation_id=self.cid, payload={"title": "intern"})

    def tearDown(self):
        pulse_db._SCHEMA_READY.discard(self.db_path)
        self.patch.stop()
        self.tmp.cleanup()

    def test_ticket_manager_sees_the_whole_ticket_across_surfaces(self):
        found = ev.history(correlation_id=self.cid, perms=TICKET)
        self.assertEqual(sorted(e["action"] for e in found), ["ticket_claimed_dashboard", "ticket_closed", "ticket_created"])
        self.assertEqual({e["domain"] for e in found}, {"ticket"})
        self.assertEqual({e["actor_name"] for e in found}, {"Anna", "Ben"})

    def test_event_types_are_unchanged_for_legacy_readers(self):
        with pulse_db.connect() as db:
            types = {r[0] for r in db.execute("SELECT event_type FROM events WHERE target_id='ticket_ab12'")}
        self.assertIn("ticket_claimed_dashboard", types)
        self.assertNotIn("ticket.claimed", types)

    def test_admin_still_sees_everything_with_that_id(self):
        self.assertEqual(len(ev.history(correlation_id=self.cid, perms=ADMIN)), 4)

    def test_other_rights_cannot_follow_a_ticket_id(self):
        for perms in (NOBODY, TASKER, {}, None):
            with self.assertRaises(PermissionError):
                ev.history(correlation_id=self.cid, perms=perms)

    def test_a_ticket_right_cannot_follow_other_prefixes_or_empty_ids(self):
        for bad in ("task:ticket_ab12", "case:x", "ticket:", "ticket", "BM:MOD-1A2B3C"):
            with self.assertRaises(PermissionError, msg=bad):
                ev.history(correlation_id=bad, perms=TICKET)

    def test_correlation_for(self):
        self.assertEqual(ev.correlation_for("task", " t1 "), "task:t1")
        with self.assertRaises(ValueError):
            ev.correlation_for("privacy", "x")
        self.assertEqual(len(ev.correlation_for("ticket", "x" * 500)), 128)


if __name__ == "__main__":
    unittest.main()
