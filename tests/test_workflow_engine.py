"""Workflow versions, safe retries and multi-stage approvals of /ultimate/automations (#144, #145, #148, #149).

Real FastAPI routes and the real engine (pulse_ultimate + pulse_workflows), temp database and config, no Discord.
Negative tests always check that nothing ran and nothing was written.
"""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from fastapi import HTTPException
from fastapi.testclient import TestClient

import pulse_automation_rules as rules
import pulse_db
import pulse_ultimate as pu
import pulse_workflows as wf
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, GUILD, TEAMLER

MANAGER = dict(TEAMLER, can_promote=True)
ALERT_CHANNEL = 444444444444444444
PAST = "2000-01-01T00:00:00+00:00"


def auth_as(perms, user_id="123", name="Tester"):
    def fake_auth(request, session, perm="can_view_dashboard", admin=False):
        if admin and not perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="Dieser Bereich ist nur für Administratoren.")
        if perm and not perms.get(perm):
            raise HTTPException(status_code=403, detail="Dafür fehlt dir die Berechtigung.")
        return SimpleNamespace(user={"id": user_id, "global_name": name}, guild=GUILD, guild_name=GUILD.name,
                               perms=perms, config={"team_role_ids": []}, member=None, msg="", msg_ok=True)
    return fake_auth


def http_error(status=503):
    return discord.HTTPException(SimpleNamespace(status=status, reason="x"), "kaputt")


class WorkflowBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config_path = Path(self.tmp.name) / "config.json"
        self.config_path.write_text(json.dumps({"pulse_antinuke": {"enabled": True, "mode": "alert", "alert_channel_id": ALERT_CHANNEL}}),
                                    encoding="utf-8")
        self.channel = SimpleNamespace(id=ALERT_CHANNEL, name="sicherheit", send=AsyncMock())
        self.guild = SimpleNamespace(id=1, members=[], get_channel=lambda cid: self.channel if cid == ALERT_CHANNEL else None,
                                     get_member=lambda _uid: None)
        self.bot = SimpleNamespace(get_guild=lambda _gid: self.guild)
        self.patches = [
            patch.object(ws, "CONFIG_FILE", str(self.config_path)),
            patch.object(ws, "auth", auth_as(ADMIN)),
            patch.object(ws, "log_audit", lambda *a, **k: None),
            patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db")),
            patch.object(pu, "_INIT", False),
            patch.object(pu, "_EVENT_TRIGGER_CACHE", {"db": None, "triggers": frozenset()}),
            patch.object(pu, "_EVENT_SEEN", pu.OrderedDict()),
            patch.object(pu, "_EVENT_COOLDOWNS", pu.OrderedDict()),
            patch.object(pu, "_EVENT_COUNTER", None),
            patch("builtins.print"),
        ]
        for p in self.patches:
            p.start()
        pulse_db.init_db()
        pu.setup()
        ws.app.state.bot = self.bot
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        ws.app.state.bot = None
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    def table(self, name, order="rowid"):
        with pu.cx() as c:
            return [dict(r) for r in c.execute(f"SELECT * FROM {name} ORDER BY {order}").fetchall()]

    def automations(self):
        return self.table("ultimate_automations")

    def runs(self):
        return self.table("ultimate_automation_runs", "started_at, rowid")

    def log_events(self):
        return pulse_db.events(event_type="Automation: Protokoll")

    def form(self, **change):
        data = {"name": "Protokoll täglich", "trigger_type": "interval", "interval_minutes": "60", "condition_type": "always",
                "step1_type": "activity_log", "step1_body": "Lauf von {name}", "enabled": "1"}
        data.update(change)
        return data

    def save(self, **change):
        r = self.client.post("/ultimate/automations/save", data=self.form(**change), follow_redirects=False)
        return r

    def run_now(self, automation_id):
        return self.client.post(f"/ultimate/automations/{automation_id}/run", data={"confirm": "1"}, follow_redirects=False)

    def sql(self, statement, args=()):
        with pu.cx() as c:
            c.execute(statement, args)


class VersioningTests(WorkflowBase):
    def test_every_changed_definition_becomes_a_new_version(self):
        self.save()
        [row] = self.automations()
        self.assertEqual(row["current_version"], 1)
        self.save(automation_id=row["id"], name="Umbenannt", step2_type="activity_log", step2_body="zweite Aktion")
        self.save(automation_id=row["id"], name="Umbenannt", step2_type="activity_log", step2_body="zweite Aktion")  # unchanged
        self.save(automation_id=row["id"], name="Umbenannt", step2_type="activity_log", step2_body="zweite Aktion", enabled="")
        history = wf.versions(row["id"])
        self.assertEqual([v["version"] for v in history], [2, 1])  # same definition and a pure on/off change add nothing
        self.assertEqual(history[1]["definition"]["name"], "Protokoll täglich")
        self.assertEqual(history[0]["created_by_name"], "Tester")
        self.assertEqual(self.automations()[0]["current_version"], 2)
        page = self.client.get(f"/ultimate/automations/{row['id']}/versions?a=1&b=2")
        self.assertEqual(page.status_code, 200)
        for text in ("Versionsverlauf", "Version 2", "Vergleich: Version 1", "Umbenannt", "Aktion 2", "zweite Aktion"):
            self.assertIn(text, page.text)
        self.assertIn("Versionen (2)", self.client.get("/ultimate/automations").text)
        # the run log names the version that was executed
        self.sql("UPDATE ultimate_automations SET enabled=1")
        self.run_now(row["id"])
        self.assertEqual(self.runs()[-1]["automation_version"], 2)

    def test_versions_are_immutable_in_the_database(self):
        self.save()
        [version] = self.table("ultimate_automation_versions")
        for statement in ("UPDATE ultimate_automation_versions SET definition_json='{}'", "DELETE FROM ultimate_automation_versions"):
            with self.assertRaises(sqlite3.DatabaseError):
                self.sql(statement)
        self.assertEqual(self.table("ultimate_automation_versions"), [version])
        # deleting the automation keeps its history, like the run log
        self.client.post(f"/ultimate/automations/{version['automation_id']}/delete", data={"confirm": "1"})
        self.assertEqual(self.automations(), [])
        self.assertEqual(self.table("ultimate_automation_versions"), [version])

    def test_restore_is_confirmed_creates_a_new_version_and_pauses(self):
        self.save()
        [row] = self.automations()
        self.save(automation_id=row["id"], name="Zweite Fassung", interval_minutes="120")
        path = f"/ultimate/automations/{row['id']}/versions/1/restore"
        r = self.client.post(path, data={}, follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])
        self.assertEqual((self.automations()[0]["name"], len(wf.versions(row["id"]))), ("Zweite Fassung", 2))
        r = self.client.post(path, data={"confirm": "1"}, follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        [restored] = self.automations()
        self.assertEqual((restored["name"], restored["interval_minutes"], restored["enabled"], restored["current_version"]),
                         ("Protokoll täglich", 60, 0, 3))
        history = wf.versions(row["id"])
        self.assertEqual([v["version"] for v in history], [3, 2, 1])
        self.assertEqual(history[0]["definition"], history[2]["definition"])
        self.assertIn("Wiederhergestellt aus Version 1", history[0]["note"])
        self.assertTrue(pulse_db.events(event_type="Automation-Version wiederhergestellt"))
        # restoring what is already current, or an unknown version, changes nothing
        r = self.client.post(f"/ultimate/automations/{row['id']}/versions/3/restore", data={"confirm": "1"}, follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])
        self.assertEqual(self.client.post(f"/ultimate/automations/{row['id']}/versions/99/restore", data={"confirm": "1"}).status_code, 404)
        self.assertEqual(self.client.post(f"/ultimate/automations/{row['id']}/versions/abc/restore", data={"confirm": "1"}).status_code, 404)
        self.assertEqual(len(wf.versions(row["id"])), 3)

    def test_automation_from_before_versioning_keeps_its_old_state_as_version_one(self):
        self.sql("""INSERT INTO ultimate_automations(id,name,trigger_type,interval_minutes,action_type,action_payload,enabled,
                    next_run_at,created_by_id,created_by_name,created_at) VALUES('auto_old','Alt','interval',60,'team_report',
                    '{}',1,'2000-01-01T00:00:00+00:00','1','Früher','2020-01-01')""")
        page = self.client.get("/ultimate/automations/auto_old/versions")
        self.assertIn("Noch keine Version gespeichert", page.text)
        self.assertEqual(self.table("ultimate_automation_versions"), [])  # a GET never writes
        self.save(automation_id="auto_old", name="Neu")
        history = wf.versions("auto_old")
        self.assertEqual([(v["version"], v["definition"]["name"]) for v in history], [(2, "Neu"), (1, "Alt")])
        self.assertEqual(history[1]["definition"]["steps"], [{"type": "team_report"}])
        self.assertEqual(history[1]["created_by_name"], "Früher")

    def test_rights_are_checked_for_history_and_restore(self):
        self.save(name="Sicherheit", step1_type="security_alert", step1_body="Alarm")   # v1: security step
        [row] = self.automations()
        self.save(automation_id=row["id"], name="Harmlos")                                 # v2: plain log step
        with patch.object(ws, "auth", auth_as(MANAGER, "555", "Manni")):
            self.assertEqual(self.client.get(f"/ultimate/automations/{row['id']}/versions").status_code, 200)
            # a manager must not bring a security step back through an old version
            r = self.client.post(f"/ultimate/automations/{row['id']}/versions/1/restore", data={"confirm": "1"})
            self.assertEqual(r.status_code, 403)
        self.save(automation_id=row["id"], name="Wieder Sicherheit", step1_type="security_alert", step1_body="Alarm")  # v3
        with patch.object(ws, "auth", auth_as(MANAGER, "555", "Manni")):
            self.assertEqual(self.client.get(f"/ultimate/automations/{row['id']}/versions").status_code, 403)
            self.assertEqual(self.client.post(f"/ultimate/automations/{row['id']}/versions/2/restore", data={"confirm": "1"}).status_code, 403)
        with patch.object(ws, "auth", auth_as(TEAMLER, "777", "Tess")):
            self.assertEqual(self.client.get(f"/ultimate/automations/{row['id']}/versions").status_code, 403)
            self.assertEqual(self.client.post(f"/ultimate/automations/{row['id']}/versions/2/restore", data={"confirm": "1"}).status_code, 403)
        self.assertEqual((self.automations()[0]["name"], len(wf.versions(row["id"]))), ("Wieder Sicherheit", 3))

    def test_html_in_names_is_escaped_on_the_history_page(self):
        self.save(name="<script>alert(1)</script>")
        [row] = self.automations()
        page = self.client.get(f"/ultimate/automations/{row['id']}/versions?a=1")
        self.assertNotIn("<script>alert(1)</script>", page.text)
        self.assertIn("&lt;script&gt;alert(1)&lt;/script&gt;", page.text)


class RetryTests(WorkflowBase):
    def failing_automation(self, **change):
        """Step 1 writes the activity log, step 2 posts into the alarm channel (which fails)."""
        self.save(step2_type="security_alert", step2_body="Alarm {name}", retry_max_attempts="2", retry_backoff_seconds="60", **change)
        return self.automations()[0]

    def retries(self):
        return self.table("ultimate_automation_retries")

    def process(self):
        return asyncio.run(wf.process_retries(self.bot))

    def make_due(self):
        self.sql("UPDATE ultimate_automation_retries SET next_attempt_at=? WHERE status='pending'", (PAST,))

    def test_only_the_failed_step_is_repeated_and_succeeds_once(self):
        row = self.failing_automation()
        self.channel.send.side_effect = http_error(503)
        self.run_now(row["id"])
        [retry] = self.retries()
        self.assertEqual((retry["status"], retry["attempt"], retry["max_attempts"]), ("pending", 0, 2))
        entries = json.loads(retry["steps_json"])
        self.assertEqual([(e["index"], e["step"]["type"], e["auto"]) for e in entries], [(1, "security_alert", True)])
        self.assertEqual(self.process(), 0)  # not due yet (backoff)
        self.assertEqual(self.channel.send.await_count, 1)
        self.channel.send.side_effect = None
        self.make_due()
        self.assertEqual(self.process(), 1)
        self.assertEqual(self.retries()[0]["status"], "done")
        self.assertEqual(self.channel.send.await_count, 2)   # failed once, repeated once
        self.assertEqual(len(self.log_events()), 1)           # the successful step never ran a second time
        last = self.runs()[-1]
        self.assertEqual((last["status"], json.loads(last["trigger_info"])["trigger"], json.loads(last["trigger_info"])["attempt"]),
                         ("success", "retry", 1))
        self.make_due()
        self.assertEqual(self.process(), 0)  # nothing left to repeat
        self.assertEqual(self.channel.send.await_count, 2)
        self.assertIn("Wiederholungen &amp; Dead-Letter", self.client.get("/ultimate/automations").text)

    def test_backoff_grows_attempts_are_bounded_and_dead_letter_needs_a_person(self):
        row = self.failing_automation()
        self.channel.send.side_effect = http_error(500)
        self.run_now(row["id"])
        first_due = self.retries()[0]["next_attempt_at"]
        self.make_due()
        self.process()
        [retry] = self.retries()
        self.assertEqual((retry["status"], retry["attempt"]), ("pending", 1))
        gap = pu.datetime.fromisoformat(retry["next_attempt_at"]) - pu.utcnow()
        self.assertTrue(100 <= gap.total_seconds() <= 125, gap)  # second wait is 2 × 60 s
        self.assertGreater(retry["next_attempt_at"], first_due)
        self.make_due()
        self.process()
        [retry] = self.retries()
        self.assertEqual((retry["status"], retry["attempt"]), ("dead", 2))
        self.assertIn("Discord-Fehler HTTP 500", retry["last_error"])
        self.assertTrue(pulse_db.events(event_type="Automation: Dead-Letter"))
        self.make_due()
        self.assertEqual(self.process(), 0)
        self.assertEqual(self.channel.send.await_count, 3)  # original run + exactly two retries
        # manual resume: confirmation required, then exactly one more attempt
        path = f"/ultimate/automations/retries/{retry['id']}/resume"
        self.assertIn("t=err", self.client.post(path, data={}, follow_redirects=False).headers["location"])
        self.assertEqual(self.retries()[0]["status"], "dead")
        self.channel.send.side_effect = None
        self.assertIn("t=ok", self.client.post(path, data={"confirm": "1"}, follow_redirects=False).headers["location"])
        self.assertEqual((self.retries()[0]["status"], self.retries()[0]["max_attempts"]), ("pending", 3))
        self.process()
        self.assertEqual(self.retries()[0]["status"], "done")
        self.assertEqual(self.channel.send.await_count, 4)
        self.assertEqual(len(self.log_events()), 1)
        self.assertIn("t=err", self.client.post(path, data={"confirm": "1"}, follow_redirects=False).headers["location"])  # done ≠ dead

    def test_unfixable_failures_and_lockdowns_are_never_repeated_automatically(self):
        self.config_path.write_text(json.dumps({"pulse_antinuke": {"enabled": True}}), encoding="utf-8")  # no alarm channel
        row = self.failing_automation()
        self.run_now(row["id"])
        [retry] = self.retries()
        self.assertEqual(retry["status"], "dead")
        self.assertFalse(json.loads(retry["steps_json"])[0]["auto"])
        self.make_due()
        self.assertEqual(self.process(), 0)
        self.assertFalse(wf._auto_retryable({"type": "antinuke_lockdown"}, "Discord-Fehler HTTP 503"))
        self.assertTrue(wf._auto_retryable({"type": "security_alert"}, "Discord-Fehler HTTP 503"))
        self.assertFalse(wf._auto_retryable({"type": "security_alert"}, "Discord hat das Senden abgelehnt (Rechte im Alarmkanal)"))

    def test_no_queue_without_retry_policy_or_in_test_mode(self):
        self.channel.send.side_effect = http_error()
        self.save(step2_type="security_alert", step2_body="x")  # default: retries off
        self.run_now(self.automations()[0]["id"])
        self.assertEqual(self.runs()[-1]["status"], "error")
        self.save(name="Test", step2_type="security_alert", step2_body="x", retry_max_attempts="3", test_mode="1")
        self.run_now(self.automations()[1]["id"])
        self.assertEqual(self.retries(), [])

    def test_interrupted_attempt_goes_to_dead_letter_instead_of_running_twice(self):
        row = self.failing_automation()
        self.channel.send.side_effect = http_error()
        self.run_now(row["id"])
        self.channel.send.reset_mock(side_effect=True)
        self.sql("UPDATE ultimate_automation_retries SET status='running', next_attempt_at=?", (PAST,))  # crashed mid-attempt
        self.assertEqual(self.process(), 0)
        [retry] = self.retries()
        self.assertEqual((retry["status"], retry["last_error"]), ("dead", wf.INTERRUPTED))
        self.channel.send.assert_not_awaited()

    def test_one_queue_entry_per_run_and_the_queue_is_bounded(self):
        row = self.failing_automation()
        steps = pu.automation_steps(row)
        done = [{"type": "activity_log", "status": "ok", "detail": ""}, {"type": "security_alert", "status": "failed", "detail": "Discord-Fehler HTTP 503"}]
        first = wf.schedule_retry(row, "run_a", steps, done, {"actor_id": "1", "member": object()}, {"trigger": "manual"})
        self.assertIsNotNone(first)
        self.assertIsNone(wf.schedule_retry(row, "run_a", steps, done, {}, {"trigger": "manual"}))  # same run: idempotent
        self.assertEqual(json.loads(self.retries()[0]["context_json"]), {"actor_id": "1"})  # objects never enter the queue
        with patch.object(wf, "MAX_PENDING_RETRIES", 1):
            wf.schedule_retry(row, "run_b", steps, done, {}, {"trigger": "manual"})
        self.assertEqual([r["status"] for r in self.retries()], ["pending", "dead"])
        self.assertIn("voll", self.retries()[1]["last_error"])
        self.assertIsNone(wf.schedule_retry(row, "run_c", steps, [dict(done[0]), dict(done[0])], {}, {}))  # nothing failed

    def test_changed_paused_or_deleted_automations_do_not_retry(self):
        self.channel.send.side_effect = http_error()
        row = self.failing_automation()
        self.run_now(row["id"])
        self.save(automation_id=row["id"], name="Geändert", step2_type="security_alert", step2_body="Alarm", retry_max_attempts="2",
                  retry_backoff_seconds="60")
        self.assertEqual(self.retries()[0]["status"], "dead")          # definition changed → waits for a person
        self.assertIn("geändert", self.retries()[0]["last_error"])
        self.run_now(row["id"])
        self.client.post(f"/ultimate/automations/{row['id']}/toggle")  # paused
        self.make_due()
        self.process()
        self.assertEqual([r["status"] for r in self.retries()], ["dead", "dead"])
        self.assertEqual(self.channel.send.await_count, 2)             # the two original runs only
        self.sql("UPDATE ultimate_automation_retries SET status='pending', next_attempt_at=?", (PAST,))
        self.client.post(f"/ultimate/automations/{row['id']}/delete", data={"confirm": "1"})
        self.assertEqual({r["status"] for r in self.retries()}, {"cancelled"})
        self.process()
        self.assertEqual(self.channel.send.await_count, 2)

    def test_queue_actions_follow_the_automation_rights(self):
        self.channel.send.side_effect = http_error()
        row = self.failing_automation()  # contains a security step → administrators only
        self.run_now(row["id"])
        self.sql("UPDATE ultimate_automation_retries SET status='dead'")
        retry_id = self.retries()[0]["id"]
        for perms in (MANAGER, TEAMLER):
            with patch.object(ws, "auth", auth_as(perms, "555", "Manni")):
                for action in ("resume", "cancel"):
                    r = self.client.post(f"/ultimate/automations/retries/{retry_id}/{action}", data={"confirm": "1"})
                    self.assertEqual(r.status_code, 403, (action, perms.get("can_promote")))
        self.assertEqual(self.retries()[0]["status"], "dead")
        self.assertEqual(self.client.post("/ultimate/automations/retries/retry_unknown/cancel").status_code, 404)
        self.assertIn("t=ok", self.client.post(f"/ultimate/automations/retries/{retry_id}/cancel", follow_redirects=False).headers["location"])
        self.assertEqual(self.retries()[0]["status"], "cancelled")


class ApprovalTests(WorkflowBase):
    TWO_STAGES = {"approval1_role": "manager", "approval1_label": "Teamleitung", "approval1_min": "1",
                  "approval2_role": "admin", "approval2_label": "Projektleitung", "approval2_min": "1", "approval_timeout": "120"}

    def approvals(self):
        return self.table("ultimate_automation_approvals")

    def decide(self, request_id, decision, perms, user_id, name, comment=""):
        with patch.object(ws, "auth", auth_as(perms, user_id, name)):
            return self.client.post(f"/ultimate/automations/approvals/{request_id}/decide",
                                    data={"decision": decision, "comment": comment}, follow_redirects=False)

    def requested(self, **change):
        self.save(**{**self.TWO_STAGES, **change})
        row = self.automations()[0]
        self.run_now(row["id"])
        return row, self.approvals()[0]

    def test_stages_are_decided_in_order_by_different_authorised_people(self):
        row, request = self.requested()
        self.assertEqual((request["status"], request["current_stage"], request["requested_by_id"]), ("pending", 0, "123"))
        self.assertEqual(self.runs()[-1]["status"], "approval")
        self.assertEqual(self.log_events(), [])  # nothing ran yet
        # the requester never approves the own run (four-eyes), a plain team member decides nothing
        self.assertEqual(self.decide(request["id"], "approve", ADMIN, "123", "Tester").status_code, 403)
        self.assertEqual(self.decide(request["id"], "approve", TEAMLER, "777", "Tess").status_code, 403)
        self.assertEqual(self.approvals()[0]["decisions_json"], "[]")
        # stage 1: manager
        r = self.decide(request["id"], "approve", MANAGER, "555", "Manni")
        self.assertIn("t=ok", r.headers["location"])
        self.assertEqual((self.approvals()[0]["status"], self.approvals()[0]["current_stage"]), ("pending", 1))
        self.assertEqual(self.log_events(), [])
        # stage 2 belongs to administrators; the same person never decides twice
        self.assertEqual(self.decide(request["id"], "approve", MANAGER, "556", "Mona").status_code, 403)
        again = self.decide(request["id"], "approve", ADMIN, "555", "Manni")
        self.assertIn("t=err", again.headers["location"])
        self.assertEqual(self.log_events(), [])
        r = self.decide(request["id"], "approve", ADMIN, "999", "Ada")
        self.assertIn("t=ok", r.headers["location"])
        [done] = self.approvals()
        self.assertEqual(done["status"], "executed")
        self.assertEqual([(d["stage"], d["user_name"], d["decision"]) for d in json.loads(done["decisions_json"])],
                         [(0, "Manni", "approve"), (1, "Ada", "approve")])
        self.assertEqual(len(self.log_events()), 1)  # ran exactly once
        last = self.runs()[-1]
        self.assertEqual((last["status"], last["id"], json.loads(last["trigger_info"])["approval_id"]), ("success", done["run_id"], request["id"]))
        # a finished request cannot be decided or executed again
        self.assertIn("t=err", self.decide(request["id"], "approve", ADMIN, "1000", "Bea").headers["location"])
        asyncio.run(wf.process_approvals(self.bot))
        self.assertEqual(asyncio.run(wf.execute_approved(self.bot, request["id"]))["status"], "skipped")
        self.assertEqual(len(self.log_events()), 1)
        page = self.client.get("/ultimate/automations").text
        for text in ("Freigaben", "Teamleitung", "Projektleitung", "Manni", "✅ ausgeführt"):
            self.assertIn(text, page)

    def test_rejection_needs_a_reason_and_stops_everything(self):
        row, request = self.requested()
        r = self.decide(request["id"], "reject", MANAGER, "555", "Manni")
        self.assertIn("t=err", r.headers["location"])
        self.assertEqual(self.approvals()[0]["status"], "pending")
        r = self.decide(request["id"], "reject", MANAGER, "555", "Manni", "nicht jetzt")
        self.assertIn("t=ok", r.headers["location"])
        self.assertEqual(self.approvals()[0]["status"], "rejected")
        asyncio.run(wf.process_approvals(self.bot))
        self.assertIn("t=err", self.decide(request["id"], "approve", ADMIN, "999", "Ada").headers["location"])
        self.assertEqual(self.log_events(), [])
        self.assertIn("t=err", self.decide(request["id"], "vielleicht", ADMIN, "999", "Ada").headers["location"])

    def test_quorum_needs_distinct_people(self):
        row, request = self.requested(approval1_min="2", approval2_role="")
        self.decide(request["id"], "approve", MANAGER, "555", "Manni")
        self.assertEqual((self.approvals()[0]["status"], self.approvals()[0]["current_stage"]), ("pending", 0))
        self.assertIn("t=err", self.decide(request["id"], "approve", MANAGER, "555", "Manni").headers["location"])
        self.assertEqual(self.log_events(), [])
        self.decide(request["id"], "approve", MANAGER, "556", "Mona")
        self.assertEqual(self.approvals()[0]["status"], "executed")
        self.assertEqual(len(self.log_events()), 1)

    def test_deadline_changed_definition_and_pause_end_the_request(self):
        row, request = self.requested()
        self.sql("UPDATE ultimate_automation_approvals SET expires_at=?", (PAST,))
        asyncio.run(wf.process_approvals(self.bot))
        self.assertEqual(self.approvals()[0]["status"], "expired")
        self.assertIn("t=err", self.decide(request["id"], "approve", MANAGER, "555", "Manni").headers["location"])
        # a new request, then the definition changes
        self.run_now(row["id"])
        second = self.approvals()[1]
        self.save(automation_id=row["id"], name="Andere Fassung", **self.TWO_STAGES)
        self.assertEqual(self.approvals()[1]["status"], "cancelled")
        self.assertIn("t=err", self.decide(second["id"], "approve", MANAGER, "555", "Manni").headers["location"])
        # approved, but paused before it could run
        self.run_now(row["id"])
        third = self.approvals()[2]
        self.sql("UPDATE ultimate_automation_approvals SET status='approved' WHERE id=?", (third["id"],))
        self.client.post(f"/ultimate/automations/{row['id']}/toggle")
        asyncio.run(wf.process_approvals(self.bot))
        self.assertEqual(self.approvals()[2]["status"], "cancelled")
        self.assertEqual(self.log_events(), [])

    def test_one_open_request_per_automation_and_test_runs_need_none(self):
        row, request = self.requested()
        self.run_now(row["id"])
        self.assertEqual(len(self.approvals()), 1)
        self.assertEqual([r["status"] for r in self.runs()], ["approval", "skipped"])
        self.client.post(f"/ultimate/automations/{row['id']}/test")
        self.assertEqual(self.runs()[-1]["status"], "test")
        self.assertEqual(len(self.approvals()), 1)
        self.assertEqual(self.log_events(), [])

    def test_scheduled_and_event_triggers_wait_for_approval_too(self):
        self.save(**self.TWO_STAGES)
        self.sql("UPDATE ultimate_automations SET next_run_at=?", (PAST,))
        with patch.object(ws, "cleanup_expired_warn_restrictions", AsyncMock(), create=True), \
                patch.object(ws, "send_interview_reminders", AsyncMock(), create=True):
            asyncio.run(pu._automation_cycle(None))
        [request] = self.approvals()
        self.assertEqual((request["status"], request["requested_by_id"]), ("pending", ""))
        self.assertEqual(self.log_events(), [])
        # without a requesting person the administrator may approve directly; stage 1 still needs someone else first
        self.decide(request["id"], "approve", MANAGER, "555", "Manni")
        self.decide(request["id"], "approve", ADMIN, "123", "Tester")
        self.assertEqual(self.approvals()[0]["status"], "executed")
        self.assertEqual(len(self.log_events()), 1)

    def test_approved_request_survives_a_restart_and_interrupted_execution_is_not_repeated(self):
        row, request = self.requested()
        self.sql("UPDATE ultimate_automation_approvals SET status='approved'")
        self.assertEqual(asyncio.run(wf.process_approvals(self.bot)), 1)  # picked up by the automation loop
        self.assertEqual((self.approvals()[0]["status"], len(self.log_events())), ("executed", 1))
        self.run_now(row["id"])
        self.sql("UPDATE ultimate_automation_approvals SET status='executing' WHERE status='pending'")  # crashed while running
        asyncio.run(wf.process_approvals(self.bot))
        self.assertEqual((self.approvals()[1]["status"], self.approvals()[1]["finish_note"]), ("failed", wf.INTERRUPTED))
        self.assertEqual(len(self.log_events()), 1)

    def test_security_automations_are_approved_by_administrators_only(self):
        r = self.save(name="Sicherheit", step1_type="security_alert", step1_body="x", **self.TWO_STAGES)
        self.assertIn("t=err", r.headers["location"])  # a manager stage is refused for security automations
        self.assertEqual(self.automations(), [])
        self.save(name="Sicherheit", step1_type="security_alert", step1_body="x", approval1_role="admin", approval1_min="1")
        row = self.automations()[0]
        self.run_now(row["id"])
        request = self.approvals()[0]
        self.assertEqual(self.decide(request["id"], "approve", MANAGER, "555", "Manni").status_code, 403)
        with patch.object(ws, "auth", auth_as(MANAGER, "555", "Manni")):
            self.assertEqual(self.client.post(f"/ultimate/automations/approvals/{request['id']}/cancel").status_code, 403)
            self.assertNotIn(request["id"], self.client.get("/ultimate/automations").text)
        self.assertEqual(self.approvals()[0]["status"], "pending")
        self.channel.send.assert_not_awaited()

    def test_cancel_is_for_the_requester_or_administrators(self):
        row, request = self.requested()
        with patch.object(ws, "auth", auth_as(MANAGER, "555", "Manni")):
            self.assertEqual(self.client.post(f"/ultimate/automations/approvals/{request['id']}/cancel").status_code, 403)
        self.assertEqual(self.approvals()[0]["status"], "pending")
        r = self.client.post(f"/ultimate/automations/approvals/{request['id']}/cancel", follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        self.assertEqual(self.approvals()[0]["status"], "cancelled")
        self.assertEqual(self.client.post("/ultimate/automations/approvals/appr_unknown/decide", data={"decision": "approve"}).status_code, 404)
        self.assertEqual(self.log_events(), [])

    def test_personal_export_contains_requests_decisions_and_versions(self):
        import pulse_retention
        row, request = self.requested()
        self.decide(request["id"], "approve", MANAGER, "555", "Manni")
        path = pulse_db.DB_PATH
        self.assertEqual(pulse_retention._approval_ids_for_decider(path, "555"), {request["id"]})
        self.assertEqual(pulse_retention._approval_ids_for_decider(path, "55"), set())       # no prefix matches
        self.assertEqual(pulse_retention._approval_ids_for_decider(path, '5" OR 1'), set())  # only real ids
        decider, _ = pulse_retention._rows_for(path, "555", extra_values_by_table={
            "ultimate_automation_approvals": {"id": pulse_retention._approval_ids_for_decider(path, "555")}})
        self.assertEqual([r["id"] for r in decider["ultimate_automation_approvals"]], [request["id"]])
        requester, _ = pulse_retention._rows_for(path, "123")
        self.assertEqual([r["id"] for r in requester["ultimate_automation_approvals"]], [request["id"]])
        self.assertEqual([r["version"] for r in requester["ultimate_automation_versions"]], [1])
        stranger, _ = pulse_retention._rows_for(path, "999", extra_values_by_table={
            "ultimate_automation_approvals": {"id": pulse_retention._approval_ids_for_decider(path, "999")}})
        self.assertNotIn("ultimate_automation_approvals", stranger)

    def test_approvers_are_notified_in_their_inbox(self):
        team_role = SimpleNamespace(id=100)
        members = [SimpleNamespace(id=555, bot=False, roles=[team_role]), SimpleNamespace(id=123, bot=False, roles=[team_role]),
                   SimpleNamespace(id=777, bot=False, roles=[team_role])]
        self.guild.members = members
        self.config_path.write_text(json.dumps({"team_role_ids": [100]}), encoding="utf-8")
        perms = {555: MANAGER, 123: ADMIN, 777: TEAMLER}
        with patch.object(ws, "compute_perms", lambda guild, uid, cfg: (perms[uid], None)), \
                patch.object(pulse_db, "notify") as notify:
            self.requested()
        self.assertEqual([call.args[0] for call in notify.call_args_list], [555])  # not the requester, not plain members
        self.assertIn("Freigabe erforderlich", notify.call_args.args[1])


class WorkflowRulesTests(unittest.TestCase):
    def test_retry_policy_is_bounded(self):
        self.assertEqual(rules.normalize_retry({}), {"max_attempts": 0, "backoff_seconds": 120})
        self.assertEqual(rules.normalize_retry({"max_attempts": "5", "backoff_seconds": "30"}), {"max_attempts": 5, "backoff_seconds": 30})
        for bad in ({"max_attempts": "6"}, {"max_attempts": "-1"}, {"max_attempts": "2", "backoff_seconds": "5"},
                    {"max_attempts": "1", "backoff_seconds": "99999"}, {"max_attempts": "x"}):
            with self.assertRaises(ValueError):
                rules.normalize_retry(bad)
        self.assertEqual([rules.backoff_delay(60, n) for n in (1, 2, 3, 4)], [60, 120, 240, 480])
        self.assertEqual(rules.backoff_delay(3600, 50), rules.RETRY_DELAY_CEILING)

    def test_approval_stages_are_validated(self):
        value = rules.normalize_approval({"stages": [{"role": ""}, {"role": "manager", "min_approvals": "2"}], "timeout_minutes": "30"})
        self.assertEqual(value, {"stages": [{"label": "Führungskraft", "role": "manager", "min_approvals": 2}], "timeout_minutes": 30})
        self.assertEqual(rules.normalize_approval(None)["stages"], [])
        for bad in ({"stages": [{"role": "owner"}]}, {"stages": [{"role": "admin", "min_approvals": "9"}]},
                    {"stages": [{"role": "admin"}] * 4}, {"stages": [{"role": "admin"}], "timeout_minutes": "1"}, {"stages": ["x"]}):
            with self.assertRaises(ValueError):
                rules.normalize_approval(bad)
        with self.assertRaises(ValueError):
            rules.normalize_approval({"stages": [{"role": "manager"}]}, security=True)

    def test_diff_names_changed_fields_and_steps(self):
        a = rules.definition_snapshot({"name": "A", "trigger_type": "interval", "steps": [{"type": "activity_log", "text": "x"}]})
        b = rules.definition_snapshot({"name": "B", "trigger_type": "interval", "test_mode": True,
                                       "steps": [{"type": "activity_log", "text": "x"}, {"type": "team_report"}]})
        self.assertEqual(rules.diff_definitions(a, a), [])
        self.assertEqual([(c["field"], c["before"], c["after"]) for c in rules.diff_definitions(a, b)],
                         [("name", "A", "B"), ("steps[2]", "—", '{"type": "team_report"}'), ("test_mode", "nein", "ja")])
        self.assertNotEqual(rules.definition_hash(a), rules.definition_hash(b))
        text = rules.describe({**b, "approval": {"stages": [{"label": "Leitung", "role": "admin", "min_approvals": 2}]},
                               "retry": {"max_attempts": 3}})
        self.assertIn("Vorher Freigabe: Leitung (2×)", text)
        self.assertIn("bis zu 3× wiederholt", text)


if __name__ == "__main__":
    unittest.main()
