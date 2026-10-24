"""Automations at /ultimate/automations: create, edit, disable, test, run, delete, refuse manipulated input,
event triggers from Anti-Nuke (dedupe, threshold, recursion guard) and bounded run history.

Real FastAPI routes and the real engine (pulse_ultimate), temp database and config, no Discord connection.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import pulse_automation_rules as rules
import pulse_db
import pulse_ultimate as pu
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for

MANAGER = dict(TEAMLER, can_promote=True)
ALERT_CHANNEL = 444444444444444444


class AutomationRouteTests(unittest.TestCase):
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
            patch.object(ws, "auth", fake_auth_for(ADMIN)),
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

    def rows(self):
        with pu.cx() as c:
            return [dict(r) for r in c.execute("SELECT * FROM ultimate_automations").fetchall()]

    def runs(self, automation_id=None):
        with pu.cx() as c:
            sql, args = "SELECT * FROM ultimate_automation_runs", ()
            if automation_id:
                sql, args = sql + " WHERE automation_id=?", (automation_id,)
            return [dict(r) for r in c.execute(sql + " ORDER BY started_at", args).fetchall()]

    def form(self, **change):
        data = {"name": "Protokoll täglich", "trigger_type": "interval", "interval_minutes": "60", "condition_type": "always",
                "step1_type": "activity_log", "step1_body": "Lauf von {name}", "enabled": "1"}
        data.update(change)
        return data

    def test_create_edit_disable_run_and_delete(self):
        r = self.client.post("/ultimate/automations/save", data=self.form(), follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        [row] = self.rows()
        self.assertEqual(json.loads(row["steps_json"]), [{"type": "activity_log", "text": "Lauf von {name}"}])
        self.assertEqual((row["trigger_type"], row["interval_minutes"], row["enabled"]), ("interval", 60, 1))
        # edit
        self.client.post("/ultimate/automations/save", data=self.form(automation_id=row["id"], name="Neu", step2_type="security_alert",
                                                                       step2_body="Hinweis {name}"))
        [row] = self.rows()
        self.assertEqual((row["name"], [s["type"] for s in json.loads(row["steps_json"])]), ("Neu", ["activity_log", "security_alert"]))
        page = self.client.get(f"/ultimate/automations?edit={row['id']}")
        self.assertIn("Automation bearbeiten", page.text)
        self.assertIn("Neu", page.text)
        # really run it (confirmation required)
        r = self.client.post(f"/ultimate/automations/{row['id']}/run", data={"confirm": "0"}, follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])
        self.assertEqual(self.runs(), [])
        r = self.client.post(f"/ultimate/automations/{row['id']}/run", data={"confirm": "1"}, follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        [run] = self.runs()
        self.assertEqual((run["status"], run["actor_name"]), ("success", "Tester"))
        self.assertEqual([s["status"] for s in json.loads(run["steps_json"])], ["ok", "ok"])
        self.assertEqual(json.loads(pulse_db.events(event_type="Automation: Protokoll")[0]["payload_json"])["text"], "Lauf von Neu")
        self.channel.send.assert_awaited_once()
        self.assertIn("Hinweis Neu", self.channel.send.await_args.args[0])
        # disable, history page, delete
        self.client.post(f"/ultimate/automations/{row['id']}/toggle")
        self.assertEqual(self.rows()[0]["enabled"], 0)
        page = self.client.get("/ultimate/automations")
        for text in ("Letzte 30 Ausführungen", "Automation fortsetzen", "durch Tester", "Aktivitätsprotokoll-Eintrag"):
            self.assertIn(text, page.text)
        # Regression: these forms contain an input named "confirm", which shadows confirm() inside inline handlers.
        self.assertNotIn("if(!confirm(", page.text)
        self.assertEqual(page.text.count("if(!window.confirm("), 2)
        r = self.client.post(f"/ultimate/automations/{row['id']}/delete", data={"confirm": "0"}, follow_redirects=False)
        self.assertIn("t=err", r.headers["location"])
        self.client.post(f"/ultimate/automations/{row['id']}/delete", data={"confirm": "1"})
        self.assertEqual(self.rows(), [])
        self.assertEqual(len(self.runs()), 1)  # history stays

    def test_test_run_and_test_mode_change_nothing(self):
        self.client.post("/ultimate/automations/save", data=self.form(test_mode="1"))
        [row] = self.rows()
        self.client.post(f"/ultimate/automations/{row['id']}/test")
        self.client.post(f"/ultimate/automations/{row['id']}/run", data={"confirm": "1"})  # test mode also on a real run
        self.assertEqual([r["status"] for r in self.runs()], ["test", "test"])
        self.assertEqual(pulse_db.events(event_type="Automation: Protokoll"), [])

    def test_failed_step_is_reported_and_the_rest_still_runs(self):
        self.config_path.write_text(json.dumps({"pulse_antinuke": {"enabled": True}}), encoding="utf-8")  # no alert channel
        self.client.post("/ultimate/automations/save", data=self.form(step1_type="security_alert", step1_body="x",
                                                                      step2_type="activity_log", step2_body="danach"))
        [row] = self.rows()
        self.client.post(f"/ultimate/automations/{row['id']}/run", data={"confirm": "1"})
        [run] = self.runs()
        self.assertEqual(run["status"], "error")
        self.assertEqual([s["status"] for s in json.loads(run["steps_json"])], ["failed", "ok"])
        self.assertIn("kein Anti-Nuke-Alarmkanal", run["result"])

    def test_manipulated_or_unsupported_automations_are_refused(self):
        cases = [
            {"trigger_type": "cron_shell"}, {"step1_type": "exec_python"}, {"step1_type": ""}, {"interval_minutes": "1"},
            {"name": ""}, {"step1_type": "notify_user", "step1_target": "abc"}, {"condition_type": "eval"},
            {"trigger_type": "antinuke_event"},  # needs at least one event
            {"step1_type": "create_task", "step1_target": "111111111111111111", "step1_title": ""},
        ]
        for change in cases:
            r = self.client.post("/ultimate/automations/save", data=self.form(**change), follow_redirects=False)
            self.assertEqual(r.status_code, 303, change)
            self.assertIn("t=err", r.headers["location"], change)
        self.assertEqual(self.client.post("/ultimate/automations/create", data={"name": "x", "action_type": "rm -rf"}).status_code, 400)
        self.assertEqual(self.client.post("/ultimate/automations/create", data={"name": "x", "action_type": "team_report",
                                                                                "trigger_type": "on_boot"}).status_code, 400)
        self.assertEqual(self.rows(), [])
        ok = self.client.post("/ultimate/automations/create", data={"name": "alt", "action_type": "team_report"}, follow_redirects=False)
        self.assertEqual(ok.status_code, 303)  # the old contract keeps working for supported actions

    def test_security_automations_are_for_administrators_only(self):
        self.client.post("/ultimate/automations/save", data=self.form(name="Sicherheit", trigger_type="antinuke_rule",
                                                                      step1_type="security_alert", step1_body="Regel {rule_label}"))
        [row] = self.rows()
        with patch.object(ws, "auth", fake_auth_for(MANAGER)):
            page = self.client.get("/ultimate/automations")
            self.assertEqual(page.status_code, 200)
            self.assertNotIn(row["id"], page.text)  # hidden from the list
            self.assertNotIn("Anti-Nuke-Regel ausgelöst", page.text)  # security triggers are not offered
            for path in (f"/ultimate/automations/{row['id']}/toggle", f"/ultimate/automations/{row['id']}/run",
                         f"/ultimate/automations/{row['id']}/delete", f"/ultimate/automations/{row['id']}/test"):
                self.assertEqual(self.client.post(path, data={"confirm": "1"}).status_code, 403, path)
            r = self.client.post("/ultimate/automations/save", data=self.form(trigger_type="antinuke_event", events=["ban"]))
            self.assertEqual(r.status_code, 403)
            self.assertEqual(self.client.get(f"/ultimate/automations?edit={row['id']}").status_code, 403)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(self.client.get("/ultimate/automations").status_code, 403)
            self.assertEqual(self.client.post("/ultimate/automations/save", data=self.form()).status_code, 403)
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["enabled"], 1)

    def dispatch(self, trigger, context):
        return asyncio.run(pu.dispatch_event(self.bot, trigger, context))

    def test_antinuke_rule_trigger_runs_once_per_event(self):
        self.client.post("/ultimate/automations/save", data=self.form(name="AN", trigger_type="antinuke_rule", rule_id="r_kanal",
                                                                      cooldown_seconds="0", step1_body="Regel {rule_label} durch {actor_id}"))
        context = {"rule_id": "r_kanal", "rule_label": "Kanäle", "actor_id": 30, "dry_run": False}
        self.assertEqual(self.dispatch("antinuke_rule", context), 1)
        self.assertEqual(self.dispatch("antinuke_rule", context), 0)  # the same event never runs twice
        self.assertEqual(self.dispatch("antinuke_rule", {**context, "rule_id": "andere"}), 0)  # other rule
        self.assertEqual(json.loads(pulse_db.events(event_type="Automation: Protokoll")[0]["payload_json"])["text"],
                         "Regel Kanäle durch 30")
        [run] = self.runs()
        self.assertEqual(json.loads(run["trigger_info"])["rule_label"], "Kanäle")

    def test_event_trigger_with_count_window_and_recursion_guard(self):
        self.client.post("/ultimate/automations/save", data=self.form(name="Massenlöschung", trigger_type="antinuke_event",
                                                                      events=["channel_delete"], min_count="3", window_seconds="10",
                                                                      group="actor", cooldown_seconds="0"))
        hits = [self.dispatch("antinuke_event", {"events": ["channel_delete"], "actor_id": actor, "audit_entry_id": i})
                for i, actor in enumerate([30, 30, 31, 30])]
        self.assertEqual(hits, [0, 0, 0, 1])  # third event of the same person, other people do not count
        token = pu._DISPATCH_DEPTH.set(1)  # inside an automation step
        try:
            self.assertEqual(self.dispatch("antinuke_event", {"events": ["channel_delete"], "actor_id": 40, "audit_entry_id": 99}), 0)
        finally:
            pu._DISPATCH_DEPTH.reset(token)
        self.assertEqual(len(self.runs()), 1)

    def test_disabled_event_automations_do_not_run_and_cache_follows_the_database(self):
        self.client.post("/ultimate/automations/save", data=self.form(trigger_type="antinuke_rule", cooldown_seconds="0"))
        [row] = self.rows()
        self.client.post(f"/ultimate/automations/{row['id']}/toggle")
        self.assertEqual(self.dispatch("antinuke_rule", {"rule_id": "x", "actor_id": 1}), 0)
        with patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "other.db")):
            self.assertEqual(asyncio.run(pu.dispatch_event(self.bot, "antinuke_rule", {"rule_id": "x"})), 0)

    def test_run_history_is_bounded(self):
        self.client.post("/ultimate/automations/save", data=self.form())
        [row] = self.rows()
        with patch.object(pu, "MAX_RUNS_PER_AUTOMATION", 3):
            for _ in range(6):
                asyncio.run(pu.execute_automation(self.bot, row, trigger_info={"trigger": "interval"}))
        self.assertEqual(len(self.runs(row["id"])), 3)

    def test_double_execution_is_prevented(self):
        self.client.post("/ultimate/automations/save", data=self.form())
        [row] = self.rows()
        pu._RUNNING.add(row["id"])
        try:
            result = asyncio.run(pu.execute_automation(self.bot, row, trigger_info={"trigger": "interval"}))
        finally:
            pu._RUNNING.discard(row["id"])
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(self.runs(), [])

    def test_scheduled_loop_runs_interval_but_not_event_automations(self):
        self.client.post("/ultimate/automations/save", data=self.form(name="Plan"))
        self.client.post("/ultimate/automations/save", data=self.form(name="Event", trigger_type="antinuke_rule"))
        with pu.cx() as c:
            c.execute("UPDATE ultimate_automations SET next_run_at='2000-01-01T00:00:00+00:00'")
        with patch.object(ws, "cleanup_expired_warn_restrictions", AsyncMock(), create=True), \
                patch.object(ws, "send_interview_reminders", AsyncMock(), create=True):
            asyncio.run(pu._automation_cycle(None))
        names = {r["id"]: r["name"] for r in self.rows()}
        self.assertEqual([names[r["automation_id"]] for r in self.runs()], ["Plan"])

    def test_legacy_automation_without_steps_still_runs(self):
        with pu.cx() as c:
            c.execute("""INSERT INTO ultimate_automations(id,name,trigger_type,interval_minutes,action_type,action_payload,enabled,
                         next_run_at,created_by_id,created_by_name,created_at) VALUES('auto_old','Alt','interval',60,'notify_user',
                         '{"user_id":"1"}',1,'2000-01-01T00:00:00+00:00','1','x','2020-01-01')""")
        [row] = self.rows()
        result = asyncio.run(pu.execute_automation(self.bot, row, trigger_info={"trigger": "interval"}))
        self.assertEqual((result["status"], result["summary"]), ("error", "target unavailable"))
        self.assertIn("Legacy-JSON", self.client.get("/ultimate/automations").text)


class AutomationRulesTests(unittest.TestCase):
    def test_placeholders_are_plain_replacements(self):
        self.assertEqual(rules.render_text("{actor_id} {__class__} {0} {name!r}", {"actor_id": 5, "name": "x"}), "5 {__class__} {0} {name!r}")

    def test_description_reads_like_when_and_then(self):
        text = rules.describe({"trigger_type": "antinuke_event", "trigger_config": {"events": ["channel_delete"], "min_count": 3,
                                                                                       "window_seconds": 10, "group": "actor"},
                               "steps": [{"type": "security_alert"}]})
        self.assertEqual(text, "Wenn „Kanal gelöscht“ mindestens 3× in 10 s von derselben Person erkannt wird, dann: "
                               "Sicherheitsmeldung in den Anti-Nuke-Alarmkanal.")


if __name__ == "__main__":
    unittest.main()
