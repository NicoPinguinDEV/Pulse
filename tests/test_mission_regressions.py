"""Regressions found by the complete security/bugfix mission (docs/audits/PULSE_COMPLETE_MISSION.md).

Each test names its finding ID. Only temp databases, fake login (tests.test_pulse_admin_area.fake_auth_for),
no Discord.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_next
import pulse_ultimate
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

BROWSER = {"Origin": "http://testserver"}


class _TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.audits = []
        self.patches = [
            patch.object(pulse_db, "DB_PATH", self.db_path),
            patch.object(ws, "BASE_DIR", Path(self.tmp.name)),
            patch.object(ws, "log_audit", lambda *a, **k: self.audits.append(a)),
            patch.object(pulse_ultimate, "_INIT", False),
            patch.object(pulse_next, "_INIT", False),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        pulse_next.setup_next()
        self.client = TestClient(ws.app, raise_server_exceptions=False, headers=BROWSER)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def rows(self, sql):
        con = sqlite3.connect(self.db_path)
        try:
            return con.execute(sql).fetchall()
        finally:
            con.close()


class DeniedActionLeavesNoTraceTests(_TempDb):
    """M-A1-1: a refused management action (403) counted as "meaningful activity" before the permission check
    (pulse_next.ctx_auth → touch_member), so denied requests changed next_activity."""

    def test_refused_management_posts_change_nothing(self):
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            for url, data in (("/ultimate/goals/create", {"title": "x"}),
                              ("/ultimate/goals/g1/update", {"value": "5"}),
                              ("/ultimate/announcements/create", {"title": "x", "body": "y"}),
                              ("/ultimate/approvals/a1/decide", {"decision": "approve"})):
                response = self.client.post(url, data=data, follow_redirects=False)
                self.assertEqual(response.status_code, 403, url)
        self.assertEqual(self.rows("SELECT * FROM next_activity"), [])
        self.assertEqual(self.rows("SELECT * FROM next_goals"), [])
        self.assertEqual(self.rows("SELECT * FROM next_announcements"), [])
        self.assertEqual(self.audits, [])

    def test_allowed_action_still_counts_as_activity(self):
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            response = self.client.post("/ultimate/goals/create", data={"title": "Ziel"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(self.rows("SELECT * FROM next_goals")), 1)
        # total_actions counts ctx_auth and save_audit (no reader of the number) – only presence matters here
        self.assertEqual([r[0] for r in self.rows("SELECT user_id FROM next_activity")], ["123"])


class HandoverCloseTests(_TempDb):
    """M-A1-2: closing an unknown or already closed handover answered "archiviert" and wrote an audit entry."""

    def test_unknown_handover_is_404_without_audit(self):
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            response = self.client.post("/ultimate/handovers/ho_missing/close", follow_redirects=False)
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.audits, [])

    def test_open_handover_is_closed_once(self):
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.client.post("/ultimate/handovers/create", data={"title": "Schicht", "content": "Alles ruhig"},
                             follow_redirects=False)
            (handover_id,) = self.rows("SELECT id FROM ultimate_handovers")[0]
            first = self.client.post(f"/ultimate/handovers/{handover_id}/close", follow_redirects=False)
            second = self.client.post(f"/ultimate/handovers/{handover_id}/close", follow_redirects=False)
        self.assertEqual(first.status_code, 303)
        self.assertEqual(second.status_code, 404)
        self.assertEqual(self.rows("SELECT status FROM ultimate_handovers"), [("closed",)])
        self.assertEqual(sum(1 for a in self.audits if "archiviert" in str(a)), 1)


class PositionalInsertTests(_TempDb):
    """M-F1: four dashboard forms always failed with HTTP 500 – their INSERT had a different number of
    placeholders than values (sqlite3.OperationalError): PX case creation, case evidence upload (the file was
    written before the failing INSERT and stayed behind), certificates and promotion requests."""

    def setUp(self):
        super().setUp()
        self.patches_extra = [patch.object(pulse_ultimate, "ATTACHMENTS_DIR", Path(self.tmp.name) / "attachments")]
        for p in self.patches_extra:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches_extra):
            p.stop()
        super().tearDown()

    def test_case_creation_and_evidence_upload(self):
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            created = self.client.post("/ultimate/cases/create", data={"title": "Fall", "priority": "high"},
                                       follow_redirects=False)
            self.assertEqual(created.status_code, 303)
            (case_id,) = self.rows("SELECT id FROM ultimate_cases")[0]
            upload = self.client.post(f"/ultimate/cases/{case_id}/upload", files={"file": ("beweis.txt", b"abc", "text/plain")},
                                      follow_redirects=False)
        self.assertEqual(upload.status_code, 303)
        self.assertEqual(self.rows("SELECT original_name, size_bytes, uploaded_by_id FROM ultimate_attachments"),
                         [("beweis.txt", 3, "123")])
        self.assertEqual(len(list((Path(self.tmp.name) / "attachments").iterdir())), 1)

    def test_failed_evidence_insert_leaves_no_orphaned_file(self):
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            self.client.post("/ultimate/cases/create", data={"title": "Fall"}, follow_redirects=False)
            (case_id,) = self.rows("SELECT id FROM ultimate_cases")[0]
            con = sqlite3.connect(self.db_path)
            con.execute("DROP TABLE ultimate_attachments")
            con.commit()
            con.close()
            upload = self.client.post(f"/ultimate/cases/{case_id}/upload", files={"file": ("b.txt", b"abc", "text/plain")},
                                      follow_redirects=False)
        self.assertEqual(upload.status_code, 500)
        folder = Path(self.tmp.name) / "attachments"
        self.assertEqual(list(folder.iterdir()) if folder.exists() else [], [])

    def test_certificate(self):
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            response = self.client.post("/ultimate/certificates/create",
                                        data={"user_id": "55", "title": "Grundkurs", "score": "120"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.rows("SELECT user_id, title, score, valid_until FROM ultimate_certificates"),
                         [("55", "Grundkurs", 100, None)])

    def test_promotion_request_creates_an_approval(self):
        from types import SimpleNamespace

        from tests.test_pulse_admin_area import GUILD
        person = SimpleNamespace(id=55, display_name="Max", roles=[])
        with patch.object(ws, "auth", fake_auth_for(ADMIN)), patch.object(GUILD, "get_member", lambda uid: person if uid == 55 else None):
            response = self.client.post("/ultimate/promotions/create", data={"user_id": "55", "to_rank": "Supporter"},
                                        follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(self.rows("SELECT user_id, to_rank, status, approved_by_id FROM next_promotions"),
                         [("55", "Supporter", "pending", None)])
        self.assertEqual(self.rows("SELECT object_type, status FROM next_approvals"), [("promotion", "pending")])


class _Role:
    def __init__(self, rid, position):
        self.id, self.position, self.name = rid, position, f"Rolle {rid}"

    def __ge__(self, other):
        return self.position >= other.position

    def __lt__(self, other):
        return self.position < other.position


class PromotionApprovalTests(_TempDb):
    """M-A1-3: approving a promotion (pulse_next.promote_now) never awaited member.add_roles – the new role was not
    given, the old one was removed and "Beförderung angewendet" reported (a demotion). Target and old role came
    unchecked from the request form: any role below the bot (e.g. an admin role) could be given or taken away,
    regardless of the team ranks and of the approver's own rank."""

    SUPPORTER, MODERATOR, LEAD, ADMIN_ROLE, BOT_ROLE = 10, 20, 30, 40, 50

    def setUp(self):
        super().setUp()
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        self.roles = {rid: _Role(rid, pos) for rid, pos in ((self.SUPPORTER, 1), (self.MODERATOR, 2), (self.LEAD, 3),
                                                             (self.ADMIN_ROLE, 4), (self.BOT_ROLE, 9))}
        self.person = SimpleNamespace(id=55, display_name="Max", add_roles=AsyncMock(), remove_roles=AsyncMock())
        self.guild = SimpleNamespace(get_member=lambda uid: self.person if uid == 55 else None,
                                     get_role=lambda rid: self.roles.get(rid), me=SimpleNamespace(top_role=self.roles[self.BOT_ROLE]))
        config = {"team_role_ids": [self.SUPPORTER, self.MODERATOR, self.LEAD]}
        self.cfg = patch.object(ws, "load_config", lambda: config)
        self.cfg.start()

    def tearDown(self):
        self.cfg.stop()
        super().tearDown()

    def ctx(self, admin=False, top=LEAD):
        from types import SimpleNamespace
        return SimpleNamespace(guild=self.guild, perms={"is_admin": admin, "can_approve": True},
                               member=SimpleNamespace(top_role=self.roles[top]), user={"id": "9", "global_name": "Lead"})

    def promote(self, ctx, target, old):
        import asyncio
        row = {"id": "promo_1", "user_id": "55", "target_role_id": str(target), "old_role_id": str(old),
               "from_rank": "A", "to_rank": "B"}
        return asyncio.run(pulse_next.promote_now(ctx, row))

    def test_new_role_is_really_given_before_the_old_one_is_removed(self):
        result = self.promote(self.ctx(), self.MODERATOR, self.SUPPORTER)
        self.person.add_roles.assert_awaited_once()
        self.assertIs(self.person.add_roles.await_args.args[0], self.roles[self.MODERATOR])
        self.person.remove_roles.assert_awaited_once()
        self.assertEqual(result, "Beförderung angewendet.")

    def test_discord_refusal_keeps_the_old_role(self):
        import discord
        from types import SimpleNamespace
        self.person.add_roles.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "nope")
        result = self.promote(self.ctx(), self.MODERATOR, self.SUPPORTER)
        self.person.remove_roles.assert_not_awaited()
        self.assertNotEqual(result, "Beförderung angewendet.")

    def test_non_team_roles_are_never_given_or_taken(self):
        for target, old in ((self.ADMIN_ROLE, self.SUPPORTER), (self.MODERATOR, self.ADMIN_ROLE)):
            self.person.add_roles.reset_mock()
            self.person.remove_roles.reset_mock()
            result = self.promote(self.ctx(admin=True), target, old)
            if target == self.ADMIN_ROLE:
                self.person.add_roles.assert_not_called()
                self.assertNotEqual(result, "Beförderung angewendet.")
            self.person.remove_roles.assert_not_called()

    def test_approver_cannot_promote_to_or_above_the_own_rank(self):
        result = self.promote(self.ctx(top=self.MODERATOR), self.LEAD, self.SUPPORTER)
        self.person.add_roles.assert_not_called()
        self.person.remove_roles.assert_not_called()
        self.assertNotEqual(result, "Beförderung angewendet.")
        self.promote(self.ctx(admin=True, top=self.SUPPORTER), self.LEAD, self.SUPPORTER)
        self.person.add_roles.assert_awaited_once()


class TeamStatsQueryTests(_TempDb):
    """M-C1: /ultimate/team (and every list of team members) computed member_stats per member, each reading
    2 × 5000 tickets, the shifts, the team data and the config – 50 s with 300 members and 5000 tickets, inside
    the event loop the bot shares. The shared inputs are now read once per page; the figures stay identical."""

    def test_same_figures_and_one_ticket_query_per_page(self):
        from types import SimpleNamespace

        from tests.test_pulse_admin_area import GUILD
        team_role = SimpleNamespace(id=100, name="Team")
        people = [SimpleNamespace(id=500 + i, display_name=f"P{i}", name=f"p{i}", bot=False, roles=[team_role], status="online")
                  for i in range(6)]
        for i in range(12):
            ticket = pulse_db.create_ticket(800 + i, 1, 9, "Kunde", "support")
            pulse_db.claim_ticket(ticket, str(500 + i % 3), "P")
            if i % 2:
                pulse_db.close_ticket(ticket, "ok")
        single = {p.id: pulse_ultimate.member_stats(p) for p in people}
        shared = pulse_ultimate.stats_context()
        self.assertEqual({p.id: pulse_ultimate.member_stats(p, shared) for p in people}, single)
        self.assertEqual((single[500]["tickets"], single[500]["closed_tickets"]), (4, 2))

        calls = []
        real = pulse_db.list_tickets
        with patch.object(ws, "auth", fake_auth_for(ADMIN)), patch.object(GUILD, "members", people), \
                patch.object(ws, "load_config", lambda: {"team_role_ids": [100]}), \
                patch.object(pulse_db, "list_tickets", lambda *a, **k: calls.append(1) or real(*a, **k)):
            response = self.client.get("/ultimate/team")
        self.assertEqual(response.status_code, 200)
        self.assertIn("P5", response.text)
        self.assertEqual(len(calls), 1)


class SchemaUpgradeTests(unittest.TestCase):
    """Phase D: every schema initialiser is idempotent and upgrades an old (pre-7.18) database additively –
    existing rows survive, new columns get their defaults, running it again changes nothing."""

    def test_old_automation_tables_are_upgraded_and_reruns_are_harmless(self):
        import cogs.pulse_suite as suite
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            path = str(Path(tmp) / "pulse.db")
            con = sqlite3.connect(path)
            con.executescript("""
                CREATE TABLE ultimate_automations(id TEXT PRIMARY KEY, name TEXT NOT NULL, trigger_type TEXT NOT NULL,
                    interval_minutes INTEGER DEFAULT 1440, action_type TEXT NOT NULL, action_payload TEXT DEFAULT '{}',
                    enabled INTEGER DEFAULT 1, next_run_at TEXT NOT NULL, last_run_at TEXT, created_by_id TEXT NOT NULL,
                    created_by_name TEXT NOT NULL, created_at TEXT NOT NULL);
                CREATE TABLE ultimate_automation_runs(id TEXT PRIMARY KEY, automation_id TEXT NOT NULL, status TEXT NOT NULL,
                    result TEXT DEFAULT '', started_at TEXT NOT NULL, finished_at TEXT);
                INSERT INTO ultimate_automations VALUES('auto_alt','Alt','interval',60,'notify_user','{}',1,'2026-01-01','',
                    '1','x','2026-01-01');
                INSERT INTO ultimate_automation_runs VALUES('run_alt','auto_alt','success','ok','2026-01-01','2026-01-01');
            """)
            con.commit()
            con.close()
            with patch.object(pulse_db, "DB_PATH", path), patch.object(suite, "DB_PATH", str(Path(tmp) / "suite.db")):
                for _ in range(2):
                    with patch.object(pulse_ultimate, "_INIT", False), patch.object(pulse_next, "_INIT", False):
                        pulse_db.init_db()
                        pulse_next.setup_next()
                        suite.init_db()
                con = sqlite3.connect(path)
                con.row_factory = sqlite3.Row
                row = dict(con.execute("SELECT * FROM ultimate_automations").fetchone())
                run = dict(con.execute("SELECT * FROM ultimate_automation_runs").fetchone())
                settings = con.execute("SELECT COUNT(*) FROM ultimate_settings").fetchone()[0]
                con.close()
        self.assertEqual((row["id"], row["name"], row["test_mode"], row["trigger_config"]), ("auto_alt", "Alt", 0, "{}"))
        self.assertEqual((run["id"], run["duration_ms"], run["steps_json"]), ("run_alt", 0, ""))
        self.assertGreaterEqual(settings, 3)  # defaults seeded once, not duplicated


class PositionalInsertGuardTests(unittest.TestCase):
    """M-F1 guard: every `INSERT INTO t VALUES(?,…)` without a column list has exactly as many placeholders as
    the table created by the modules has columns (otherwise that form fails with HTTP 500 on every use)."""

    def test_placeholder_count_matches_schema(self):
        import re

        import cogs.pulse_suite as suite
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            main_db, suite_db = str(Path(tmp) / "pulse.db"), str(Path(tmp) / "suite.db")
            with patch.object(pulse_db, "DB_PATH", main_db), patch.object(pulse_ultimate, "_INIT", False), \
                    patch.object(pulse_next, "_INIT", False), patch.object(suite, "DB_PATH", suite_db):
                pulse_db.init_db()
                pulse_next.setup_next()
                suite.init_db()
            columns = {}
            for path in (main_db, suite_db):
                con = sqlite3.connect(path)
                for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    columns[table] = len(con.execute(f'PRAGMA table_info("{table}")').fetchall())
                con.close()
        pattern = re.compile(r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+(\w+)\s+VALUES\s*\(([?,\s]+)\)", re.I | re.S)
        wrong = []
        for source in [*root.glob("*.py"), *root.glob("cogs/*.py")]:
            text = source.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                table, placeholders = match.group(1), match.group(2).count("?")
                if table in columns and columns[table] != placeholders:
                    line = text[:match.start()].count("\n") + 1
                    wrong.append(f"{source.name}:{line} {table}: {placeholders} Platzhalter, {columns[table]} Spalten")
        self.assertGreater(len(columns), 40)
        self.assertEqual(wrong, [])


if __name__ == "__main__":
    unittest.main()
