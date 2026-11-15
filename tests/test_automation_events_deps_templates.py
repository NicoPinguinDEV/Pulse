"""Audit #125/#141/#151/#152: Ereignis-Auslöser (domain_event), Abhängigkeiten, Vorlagenbibliothek, Ablaufdiagramm."""
import asyncio
import contextvars
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pulse_automation_rules as rules
import pulse_event_automation as bridge
import pulse_ultimate as pu
import pulse_workflow_flow as flow
import pulse_workflow_templates as library
import webserver as ws
from tests import test_automation_center as base
from tests.test_pulse_admin_area import TEAMLER, fake_auth_for

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)


class RuleTests(unittest.TestCase):
    def test_domain_event_config_is_validated(self):
        cfg = rules.normalize_trigger_config("domain_event", {"domain": "task", "action": "completed"})
        self.assertEqual((cfg["domain"], cfg["action"], cfg["cooldown_seconds"]), ("task", "completed", 60))
        self.assertEqual(rules.normalize_trigger_config("domain_event", {"domain": "ticket"})["action"], "*")
        for bad in ({}, {"domain": "gibtesnicht"}, {"domain": "task", "action": "Böse Aktion"}, {"domain": "automation"}, {"domain": "panel", "action": "x"}):
            with self.assertRaises(ValueError, msg=str(bad)):
                rules.normalize_trigger_config("domain_event", bad)

    def test_matching_wildcard_and_exact(self):
        ctx = {"domain": "task", "action": "completed"}
        self.assertTrue(rules.trigger_matches("domain_event", {"domain": "task", "action": "*"}, "domain_event", ctx))
        self.assertTrue(rules.trigger_matches("domain_event", {"domain": "task", "action": "completed"}, "domain_event", ctx))
        self.assertFalse(rules.trigger_matches("domain_event", {"domain": "task", "action": "created"}, "domain_event", ctx))
        self.assertFalse(rules.trigger_matches("domain_event", {"domain": "ticket", "action": "*"}, "domain_event", ctx))

    def test_domain_events_are_not_security_triggers(self):
        self.assertFalse(rules.needs_admin({"trigger_type": "domain_event", "steps": [{"type": "activity_log"}]}))
        self.assertTrue(rules.needs_admin({"trigger_type": "antinuke_event", "steps": []}))

    def test_dependency_config_and_state(self):
        self.assertEqual(rules.normalize_depends_on({}), {})
        self.assertEqual(rules.normalize_depends_on({"automation_id": "auto_1"})["max_age_minutes"], rules.DEFAULT_DEPENDENCY_AGE_MIN)
        for bad in ({"automation_id": "mit Leerzeichen"}, {"automation_id": "x" * 80}):
            with self.assertRaises(ValueError):
                rules.normalize_depends_on(bad)
        with self.assertRaises(ValueError):
            rules.normalize_depends_on({"automation_id": "a"}, own_id="a")
        fresh = {"status": "success", "finished_at": (NOW - timedelta(minutes=5)).isoformat()}
        self.assertEqual(rules.dependency_met(fresh, 60, NOW), (True, ""))
        self.assertFalse(rules.dependency_met({**fresh, "finished_at": (NOW - timedelta(hours=3)).isoformat()}, 60, NOW)[0])
        self.assertIn("error", rules.dependency_met({"status": "error", "finished_at": NOW.isoformat()}, 60, NOW)[1])
        self.assertFalse(rules.dependency_met(None, 60, NOW)[0])
        self.assertFalse(rules.dependency_met({"status": "success", "finished_at": "kaputt"}, 60, NOW)[0])

    def test_describe_mentions_event_and_dependency(self):
        text = rules.describe({"trigger_type": "domain_event", "steps": [{"type": "activity_log"}],
                               "trigger_config": {"domain": "task", "action": "completed", "depends_on": {"automation_id": "auto_9", "max_age_minutes": 30}}})
        self.assertIn("completed", text)
        self.assertIn("auto_9", text)


class FlowTests(unittest.TestCase):
    def test_nodes_and_escaping(self):
        auto = {"trigger_type": "domain_event", "trigger_config": {"domain": "<b>x</b>", "action": "*", "depends_on": {"automation_id": "a1", "max_age_minutes": 30}},
                "condition": {"type": "open_tickets_gte", "value": 5}, "steps": [{"type": "activity_log"}, {"type": "notify_team"}],
                "approval": {"stages": [{"role": "", "label": "Leitung", "min_approvals": 2}]}, "retry": {"max_attempts": 2}, "test_mode": True}
        kinds = [n["kind"] for n in flow.nodes(auto)]
        self.assertEqual(kinds, ["trigger", "cond", "cond", "gate", "step", "step"])
        html = flow.render(auto, lambda s: str(s).replace("<", "&lt;").replace(">", "&gt;"))
        self.assertNotIn("<b>x</b>", html)
        self.assertIn("wf-flow", html)
        self.assertIn("wiederholt", html)

    def test_legacy_single_action_is_shown(self):
        self.assertEqual([n["kind"] for n in flow.nodes({"trigger_type": "interval", "steps": [], "action_type": "team_report"})], ["trigger", "step"])


class TemplateTests(unittest.TestCase):
    def test_every_template_is_valid_safe_and_unique(self):
        ids = [t["id"] for t in library.TEMPLATES]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual({t["category"] for t in library.TEMPLATES}, {"recruiting", "dienst", "support"})
        for template in library.TEMPLATES:
            value = library.draft(template)
            self.assertFalse(value["enabled"], template["id"])
            self.assertTrue(value["test_mode"], template["id"])
            self.assertFalse(rules.needs_admin(value), template["id"])
            self.assertGreaterEqual(template["version"], 1)
            self.assertFalse(any(s["type"] in ("create_task", "notify_user") for s in value["steps"]), "Vorlagen enthalten keine Discord-IDs")
        self.assertEqual(len(library.catalog()), len(library.TEMPLATES))


class Loop:
    def __init__(self):
        self.scheduled = []

    def is_closed(self):
        return False

    def call_soon_threadsafe(self, fn):
        self.scheduled.append(fn)


def fake_ultimate(triggers=("domain_event",), depth=0):
    var = contextvars.ContextVar("d", default=depth)
    calls = []

    async def dispatch_event(bot, trigger, context):
        calls.append((trigger, context))
        return 1
    return SimpleNamespace(_EVENT_TRIGGER_CACHE={"triggers": frozenset(triggers)}, _DISPATCH_DEPTH=var, dispatch_event=dispatch_event, calls=calls)


class BridgeTests(unittest.TestCase):
    def test_schedules_only_with_an_active_trigger_and_no_recursion(self):
        loop, bot = Loop(), SimpleNamespace(loop=None)
        args = ("task_completed", "task", "t1", "900000000000000001", "Ann", "event_1")
        self.assertTrue(bridge.on_record(*args, ultimate=fake_ultimate(), bot=bot, loop=loop))
        self.assertEqual(len(loop.scheduled), 1)
        loop2 = Loop()
        self.assertFalse(bridge.on_record(*args, ultimate=fake_ultimate(triggers=()), bot=bot, loop=loop2))      # keine aktive Automation
        self.assertFalse(bridge.on_record(*args, ultimate=fake_ultimate(depth=1), bot=bot, loop=loop2))          # von einer Automation verursacht
        self.assertFalse(bridge.on_record("Automation: Protokoll", "automation", "a", "1", "x", "e", ultimate=fake_ultimate(), bot=bot, loop=loop2))
        self.assertFalse(bridge.on_record(*args, ultimate=fake_ultimate(), bot=None, loop=None))                  # kein Bot
        self.assertEqual(loop2.scheduled, [])

    def test_context_carries_domain_action_and_no_payload(self):
        ctx = bridge.build_context("task_completed", "task", "t1", "7", "Ann", "e1")
        self.assertEqual((ctx["domain"], ctx["action"], ctx["audit_entry_id"], ctx["events"]), ("task", "task_completed", "e1", ["task.task_completed"]))
        self.assertNotIn("payload", ctx)

    def test_never_raises(self):
        class Broken:
            _EVENT_TRIGGER_CACHE = None
        self.assertFalse(bridge.on_record("x", "y", "z", "1", "a", "e", ultimate=Broken(), bot=object(), loop=Loop()))


class EngineTests(base.AutomationRouteTests):
    def save(self, **change):
        r = self.client.post("/ultimate/automations/save", data=self.form(**change), follow_redirects=False)
        return r.headers["location"]

    def by_name(self, name):
        return next(r for r in self.rows() if r["name"] == name)

    def dispatch_domain(self, context):
        pu.refresh_event_triggers()
        return asyncio.run(pu.dispatch_event(self.bot, "domain_event", context))

    def test_domain_event_automation_runs_for_matching_events_only(self):
        loc = self.save(name="Aufgabe erledigt", trigger_type="domain_event", de_domain="task", de_action="completed", de_cooldown="0")
        self.assertIn("t=ok", loc)
        row = self.by_name("Aufgabe erledigt")
        self.assertEqual(json.loads(row["trigger_config"])["domain"], "task")
        self.assertEqual(self.dispatch_domain({"domain": "task", "action": "created", "actor_id": "1", "audit_entry_id": "e1"}), 0)
        self.assertEqual(self.dispatch_domain({"domain": "ticket", "action": "completed", "actor_id": "1", "audit_entry_id": "e2"}), 0)
        self.assertEqual(self.dispatch_domain({"domain": "task", "action": "completed", "actor_id": "1", "audit_entry_id": "e3"}), 1)
        self.assertEqual(self.dispatch_domain({"domain": "task", "action": "completed", "actor_id": "1", "audit_entry_id": "e3"}), 0)  # dedupe
        self.assertEqual([r["status"] for r in self.runs(row["id"])], ["success"])

    def test_a_recorded_event_starts_the_automation_end_to_end_without_recursion(self):
        import pulse_db
        self.save(name="Aufgabe erledigt", trigger_type="domain_event", de_domain="task", de_action="task_completed", de_cooldown="0")
        pu.refresh_event_triggers()
        row = self.by_name("Aufgabe erledigt")

        async def scenario():
            self.bot.loop = asyncio.get_running_loop()
            pulse_db.record_event("task_completed", "task", "t-1", "900000000000000001", "Ann", {"title": "geheim"})
            await asyncio.sleep(0.3)
            pulse_db.record_event("ticket_closed", "ticket", "x-1", "900000000000000001", "Ann", {})   # anderer Bereich
            await asyncio.sleep(0.2)
        asyncio.run(scenario())
        runs = self.runs(row["id"])
        self.assertEqual([r["status"] for r in runs], ["success"])
        self.assertNotIn("geheim", json.dumps(runs))      # die Automation bekommt keine Nutzdaten
        # der Protokolleintrag der Automation selbst startete keinen weiteren Lauf
        self.assertEqual(len(self.runs(row["id"])), 1)

    def test_domain_event_is_never_started_by_the_schedule(self):
        self.save(name="Nur Ereignis", trigger_type="domain_event", de_domain="task", de_action="*")
        with pu.cx() as c:
            c.execute("UPDATE ultimate_automations SET next_run_at='2000-01-01T00:00:00+00:00'")
        asyncio.run(pu._automation_cycle(self.bot))
        self.assertEqual(self.runs(), [])

    def test_invalid_event_config_is_refused_with_a_message(self):
        self.assertIn("t=err", self.save(name="Kaputt", trigger_type="domain_event", de_domain="automation"))
        self.assertEqual(self.rows(), [])

    def test_dependency_waits_until_the_other_automation_succeeded(self):
        self.save(name="Erst")
        first = self.by_name("Erst")
        self.save(name="Danach", dep_id=first["id"], dep_max_age="60")
        second = self.by_name("Danach")
        self.assertEqual(json.loads(second["trigger_config"])["depends_on"], {"automation_id": first["id"], "max_age_minutes": 60})
        waiting = asyncio.run(pu.execute_automation(self.bot, second, trigger_info={"trigger": "interval"}))
        self.assertEqual(waiting["status"], "skipped")
        self.assertIn("noch nie erfolgreich", waiting["summary"])
        self.assertEqual(self.runs(second["id"]), [])
        asyncio.run(pu.execute_automation(self.bot, first, trigger_info={"trigger": "interval"}))
        done = asyncio.run(pu.execute_automation(self.bot, second, trigger_info={"trigger": "interval"}))
        self.assertEqual(done["status"], "success")
        manual = asyncio.run(pu.execute_automation(self.bot, second, trigger_info={"trigger": "manual"}))
        self.assertEqual(manual["status"], "success")

    def test_dependency_ignores_test_runs_and_rejects_cycles_and_unknown_targets(self):
        self.save(name="Erst", test_mode="1")
        first = self.by_name("Erst")
        self.save(name="Danach", dep_id=first["id"])
        second = self.by_name("Danach")
        asyncio.run(pu.execute_automation(self.bot, first, trigger_info={"trigger": "interval"}))   # Testmodus -> Status "test"
        self.assertEqual(asyncio.run(pu.execute_automation(self.bot, second, trigger_info={"trigger": "interval"}))["status"], "skipped")
        self.assertIn("t=err", self.save(automation_id=first["id"], name="Erst", dep_id=second["id"]))   # Zyklus
        self.assertIn("t=err", self.save(name="Selbst", dep_id="gibtesnicht"))
        self.assertIn("t=err", self.save(automation_id=first["id"], name="Erst", dep_id=first["id"]))   # selbst

    def test_editor_shows_flow_domain_fields_and_move_buttons(self):
        self.save(name="Mit Ablauf", trigger_type="domain_event", de_domain="ticket", de_action="closed", step2_type="notify_managers", step2_title="Hinweis")
        row = self.by_name("Mit Ablauf")
        html = self.client.get(f"/ultimate/automations?edit={row['id']}").text
        for needle in ("wf-flow", 'name="de_domain"', 'name="dep_id"', "data-step-move", "data-step-row"):
            self.assertIn(needle, html)

    def test_templates_page_and_adoption_create_a_disabled_test_draft(self):
        html = self.client.get("/ultimate/automations/vorlagen").text
        self.assertIn("Support: Ticket-Frist überwachen", html)
        r = self.client.post("/ultimate/automations/vorlagen/support-sla", follow_redirects=False)
        self.assertIn("t=ok", r.headers["location"])
        [row] = self.rows()
        self.assertEqual((row["enabled"], row["test_mode"], row["name"]), (0, 1, "Support: Ticket-Frist überwachen"))
        import pulse_workflows
        self.assertTrue(any("Vorlage support-sla Version 1" in str(v.get("note", "")) for v in pulse_workflows.versions(row["id"])))
        self.assertEqual(self.client.post("/ultimate/automations/vorlagen/gibtesnicht").status_code, 404)

    def test_templates_need_a_manager(self):
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(self.client.get("/ultimate/automations/vorlagen").status_code, 403)
            self.assertEqual(self.client.post("/ultimate/automations/vorlagen/support-sla").status_code, 403)
        self.assertEqual(self.rows(), [])


# Die geerbten Tests der Basisklasse laufen schon in test_automation_center; hier nur die eigenen.
for _name in [n for n in dir(base.AutomationRouteTests) if n.startswith("test_") and n not in EngineTests.__dict__]:
    setattr(EngineTests, _name, None)


if __name__ == "__main__":
    unittest.main()
