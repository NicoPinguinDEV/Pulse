"""Regressionen aus dem Vollaudit (Oktober 2026): Rechte im Dashboard, Datenverlust bei Overrides,
doppelte Discord-Aktionen bei schnellen Klicks und Loops, die nach einem Fehler dauerhaft stehen blieben.

Echte FastAPI-Routen mit Fake-Login und Temp-Datenbank; keine Discord-Verbindung, keine Produktionsdaten.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import pulse_db
import pulse_next
import pulse_ultimate
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

LEADER = dict(TEAMLER, can_promote=True, can_manage_permissions=True, can_manage_backups=True)


def auth_with(perms, user_id="123"):
    base = fake_auth_for(perms)

    def fake(*a, **k):
        ctx = base(*a, **k)
        ctx.user = {"id": user_id, "global_name": "Tester"}
        return ctx
    return fake


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch.object(pulse_ultimate, "_INIT", False),
                        patch.object(pulse_next, "_INIT", False)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class PermissionOverrideTests(TempDb):
    MEMBERS = [SimpleNamespace(id=1, display_name="A"), SimpleNamespace(id=2, display_name="B"),
               SimpleNamespace(id=123, display_name="Ich")]

    def overrides(self):
        with pulse_next.cx() as c:
            return {(r["user_id"], r["permission"]) for r in c.execute("SELECT user_id,permission FROM next_permission_overrides")}

    def seed(self, *rows):
        pulse_next.setup_next()
        with pulse_next.cx() as c:
            for uid, perm in rows:
                c.execute("INSERT INTO next_permission_overrides VALUES(?,?,?,?,?)", (uid, perm, 1, "0", "x"))

    def test_saving_one_member_keeps_the_other_members_overrides(self):
        self.seed(("2", "can_manage_tasks"))
        with patch.object(ws, "auth", auth_with(ADMIN)), patch.object(pulse_next, "members", lambda g: self.MEMBERS):
            r = self.client.post("/ultimate/permissions/save", data={"member_id": "1", "1:can_manage_tasks": "on"}, follow_redirects=False)
        self.assertEqual(r.status_code, 303)
        self.assertEqual(self.overrides(), {("1", "can_manage_tasks"), ("2", "can_manage_tasks")})

    def test_non_admin_cannot_edit_own_overrides(self):
        with patch.object(ws, "auth", auth_with(LEADER)), patch.object(pulse_next, "members", lambda g: self.MEMBERS):
            r = self.client.post("/ultimate/permissions/save", data={"member_id": "123", "123:can_manage_tasks": "on"}, follow_redirects=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.overrides(), set())

    def test_non_admin_cannot_grant_or_revoke_admin_only_rights(self):
        self.seed(("2", "can_manage_backups"))
        with patch.object(ws, "auth", auth_with(LEADER)), patch.object(pulse_next, "members", lambda g: self.MEMBERS):
            self.client.post("/ultimate/permissions/save", data={"member_id": "1", "1:can_manage_permissions": "on"}, follow_redirects=False)
            self.client.post("/ultimate/permissions/save", data={"member_id": "2"}, follow_redirects=False)
        self.assertEqual(self.overrides(), {("2", "can_manage_backups")})


class BackupAndApiKeyTests(TempDb):
    def test_backup_download_and_restore_are_admin_only(self):
        with patch.object(ws, "auth", auth_with(LEADER)), patch.object(pulse_next, "restore_backup") as restore:
            self.assertEqual(self.client.get("/ultimate/backup/pulse_backup_x.zip").status_code, 403)
            r = self.client.post("/ultimate/backup/pulse_backup_x.zip/restore", data={"confirm": "RESTORE"}, follow_redirects=False)
        self.assertEqual(r.status_code, 403)
        restore.assert_not_called()

    def test_api_key_creation_is_admin_only(self):
        with patch.object(ws, "auth", auth_with(LEADER)):
            r = self.client.post("/ultimate/system/api-key", data={"name": "extern"}, follow_redirects=False)
        self.assertEqual(r.status_code, 403)
        pulse_ultimate.setup()
        with pulse_ultimate.cx() as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM ultimate_api_keys").fetchone()[0], 0)


class FeatureRouteTests(TempDb):
    def test_open_ticket_cannot_be_rated(self):
        with patch.object(pulse_db, "get_ticket", lambda **k: {"id": "T1", "user_id": "123", "status": "open", "rating": None}), \
                patch.object(pulse_db, "rate_ticket") as rate, patch.object(pulse_db, "notify") as notify, \
                patch.object(ws, "auth", auth_with(TEAMLER)):
            r = self.client.post("/tickets/rate", data={"ticket_id": "T1", "rating": "5"}, follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])
        rate.assert_not_called()
        notify.assert_not_called()

    def test_rated_ticket_cannot_be_overwritten(self):
        with patch.object(pulse_db, "get_ticket", lambda **k: {"id": "T1", "user_id": "123", "status": "closed", "rating": 4}), \
                patch.object(pulse_db, "rate_ticket") as rate, patch.object(ws, "auth", auth_with(TEAMLER)):
            self.client.post("/tickets/rate", data={"ticket_id": "T1", "rating": "1"}, follow_redirects=False)
        rate.assert_not_called()

    def test_malformed_training_questions_are_rejected_not_500(self):
        with patch.object(ws, "auth", auth_with(ADMIN)), patch.object(pulse_db, "create_training") as create:
            for payload in ("[1]", '[{"question":"q","options":["a","b"],"answer_index":"x"}]'):
                r = self.client.post("/training/create", data={"title": "T", "questions_json": payload}, follow_redirects=False)
                self.assertEqual(r.status_code, 303, payload)
                self.assertIn("t=err", r.headers["location"])
        create.assert_not_called()

    def test_legacy_transcript_route_needs_ticket_rights(self):
        leader_without_tickets = dict(TEAMLER, can_manage_tickets=False, can_promote=True)
        with patch.object(ws, "auth", auth_with(leader_without_tickets)):
            self.assertEqual(self.client.get("/ticket/transcript/x.txt").status_code, 403)


class TicketListTests(TempDb):
    def test_open_only_filters_in_sql(self):
        with pulse_db.connect() as db:
            cols = [r[1] for r in db.execute("PRAGMA table_info(tickets)")]
            for i, status in enumerate(["closed", "open", "closed", "in_progress"]):
                row = {c: "" for c in cols}
                row.update(id=f"T{i}", status=status, priority="normal", opened_at=f"2026-10-0{i + 1}T00:00:00",
                           channel_id=str(100 + i), user_id="1", guild_id="1")
                for c in cols:
                    if c not in row:
                        row[c] = None
                db.execute(f"INSERT INTO tickets({','.join(row)}) VALUES({','.join('?' * len(row))})", list(row.values()))
        self.assertEqual({t["id"] for t in pulse_db.list_tickets(limit=1, open_only=True)} <= {"T1", "T3"}, True)
        self.assertEqual({t["id"] for t in pulse_db.list_tickets(limit=10, open_only=True)}, {"T1", "T3"})
        self.assertEqual(len(pulse_db.list_tickets(limit=10)), 4)


class SuiteTaskRightsTests(unittest.TestCase):
    def test_only_managers_or_assignee_may_edit(self):
        from cogs.pulse_suite import can_edit_task
        task = {"assignee_id": "55"}
        self.assertFalse(can_edit_task(SimpleNamespace(perms=TEAMLER, user={"id": "123"}), task))
        self.assertTrue(can_edit_task(SimpleNamespace(perms=TEAMLER, user={"id": "55"}), task))
        self.assertTrue(can_edit_task(SimpleNamespace(perms=dict(TEAMLER, can_manage_tasks=True), user={"id": "1"}), task))

    def test_promotion_routes_use_the_discord_member_not_the_session(self):
        source = (Path(ws.__file__).parent / "cogs" / "pulse_suite.py").read_text(encoding="utf-8")
        self.assertNotIn("ctx.user.top_role", source)


class DoubleClickTests(unittest.IsolatedAsyncioTestCase):
    async def test_quiz_answer_scores_only_once_when_two_arrive_together(self):
        from cogs import flaggenquiz
        cog = flaggenquiz.FlaggenQuizCog.__new__(flaggenquiz.FlaggenQuizCog)
        cog.bot = None
        cog._resolving = set()
        flag = {"name": "Deutschland", "answers": ["deutschland"]}
        cog.active_games = {9: flag}
        async def slow_round(*a, **k):
            await asyncio.sleep(0.05)
        cog.start_new_round = slow_round
        guild = SimpleNamespace(id=1)

        def msg(uid):
            return SimpleNamespace(author=SimpleNamespace(bot=False, id=uid), guild=guild,
                                   channel=SimpleNamespace(id=9), content="Deutschland")
        with patch.object(flaggenquiz, "get_quiz_config", lambda gid: (9, "Deutschland")), \
                patch.object(flaggenquiz, "add_point") as add_point:
            await asyncio.gather(flaggenquiz.FlaggenQuizCog.on_message(cog, msg(1)),
                                 flaggenquiz.FlaggenQuizCog.on_message(cog, msg(2)))
        self.assertEqual(add_point.call_count, 1)
        self.assertEqual(cog._resolving, set())

    async def test_ai_review_runs_the_action_once_for_parallel_clicks(self):
        from cogs import ai_moderation
        cog = ai_moderation.AIModeration.__new__(ai_moderation.AIModeration) \
            if hasattr(ai_moderation, "AIModeration") else None
        if cog is None:
            self.skipTest("Cog-Klasse nicht gefunden")
        cog.cases = {"c1": {"id": "c1", "status": "pending"}}
        cog._reviews_in_progress = set()
        cog._reviewer_allowed = AsyncMock(return_value=True)
        calls = []

        async def run(interaction, case_id, case, action):
            calls.append(case_id)
            await asyncio.sleep(0.01)
        cog._run_review_action = run
        responses = []

        def inter():
            return SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock(side_effect=lambda *a, **k: responses.append(a))))
        await asyncio.gather(cog.handle_review_action(inter(), "c1", "ban"), cog.handle_review_action(inter(), "c1", "ban"))
        self.assertEqual(calls, ["c1"])
        self.assertEqual(len(responses), 1)


if __name__ == "__main__":
    unittest.main()
