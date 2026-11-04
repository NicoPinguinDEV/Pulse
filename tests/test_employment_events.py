"""Tests for the minimal employment event introduced in Phase 15.6c."""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from pulse_employment import (
    build_employment_event,
    employment_hired_at,
    store_employment_event,
)

ROOT = Path(__file__).resolve().parents[1]
VALID_EVENT = {
    "user_id": "123456789012345678",
    "hired_at": "2026-11-05T12:34:56+01:00",
    "actor_id": "987654321098765432",
    "source": "manual_hire",
    "audit_ref": "audit_a1b2c3",
}


def read_source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function {name!r} was not found")


class EmploymentEventDomainTests(unittest.TestCase):
    def test_event_contains_only_minimal_employment_metadata(self):
        event = build_employment_event(**VALID_EVENT, career_path="LOW_TEAM")

        self.assertEqual(event, {
            "version": 1,
            "user_id": VALID_EVENT["user_id"],
            "hired_at": VALID_EVENT["hired_at"],
            "actor_id": VALID_EVENT["actor_id"],
            "source": "manual_hire",
            "audit_ref": "audit_a1b2c3",
            "career_path": "low_team",
        })
        serialized = json.dumps(event)
        for forbidden in ("application_id", "answers", "notes", "reason", "review", "applicant_name"):
            self.assertNotIn(forbidden, serialized)

    def test_event_omits_unknown_or_malformed_optional_career_path(self):
        event = build_employment_event(**VALID_EVENT, career_path="Bewerber mit Notizen")
        self.assertNotIn("career_path", event)

    def test_event_requires_timezone_aware_time_and_valid_source(self):
        with self.assertRaises(ValueError):
            build_employment_event(**{**VALID_EVENT, "hired_at": "2026-11-05T12:34:56"})
        with self.assertRaises(ValueError):
            build_employment_event(**{**VALID_EVENT, "source": "application_status_accepted"})
        with self.assertRaises(ValueError):
            build_employment_event(**{**VALID_EVENT, "audit_ref": "not-an-audit-ref"})
        with self.assertRaises(ValueError):
            build_employment_event(**{**VALID_EVENT, "user_id": "not-a-discord-id"})

    def test_store_is_idempotent_for_the_same_audit_reference(self):
        entry = {"team_since": "2026-11-05T12:34:56+01:00"}
        event = build_employment_event(**VALID_EVENT)

        self.assertTrue(store_employment_event(entry, event))
        snapshot = dict(entry["employment_event"])
        self.assertFalse(store_employment_event(entry, event))
        self.assertEqual(entry["employment_event"], snapshot)

    def test_a_new_confirmed_reentry_replaces_only_the_current_tenure_event(self):
        entry = {"notes": ["existing personal record"], "team_since": "old"}
        first = build_employment_event(**VALID_EVENT)
        second = build_employment_event(**{
            **VALID_EVENT,
            "hired_at": "2026-12-01T09:00:00+01:00",
            "audit_ref": "audit_d4e5f6",
            "source": "team_role_assignment",
        })

        store_employment_event(entry, first)
        store_employment_event(entry, second)

        self.assertEqual(entry["employment_event"], second)
        self.assertEqual(entry["notes"], ["existing personal record"])

    def test_timestamp_is_returned_only_for_the_matching_user(self):
        entry = {"employment_event": build_employment_event(**VALID_EVENT)}
        self.assertEqual(employment_hired_at(entry, VALID_EVENT["user_id"]), VALID_EVENT["hired_at"])
        self.assertIsNone(employment_hired_at(entry, "111111111111111111"))
        self.assertIsNone(employment_hired_at({}, VALID_EVENT["user_id"]))


class EmploymentEventIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.web = read_source("webserver.py")
        cls.suite = read_source("cogs/pulse_suite.py")

    def test_manual_hire_records_only_after_discord_role_add_succeeds(self):
        handler = function_source(self.web, "handle_action")
        start = handler.index('if action == "hire":')
        end = handler.index('# ---------- Befördern / Degradieren / Kicken ----------', start)
        manual = handler[start:end]

        self.assertLess(manual.index("await target.add_roles("), manual.index("store_employment_event("))
        self.assertIn('source="manual_hire"', manual)
        self.assertIn("audit_ref = log_audit(", manual)
        self.assertIn('team_entry["team_since"] = hired_at', manual)

    def test_interview_hire_records_only_after_atomic_discord_role_edit(self):
        handler = function_source(self.web, "handle_action")
        start = handler.index('if decision == "hire_after_interview":')
        end = handler.index('if decision == "reject_after_interview"', start)
        hiring = handler[start:end]

        self.assertLess(hiring.index("await applicant.edit("), hiring.index("store_employment_event("))
        self.assertLess(hiring.index('item["status"] = "hired"'), hiring.index("store_employment_event("))
        self.assertIn('source="application_interview"', hiring)
        self.assertIn("career_path=item.get(\"career_path\")", hiring)

    def test_first_team_role_assignment_records_event_only_on_zero_to_one_transition(self):
        route = function_source(self.web, "team_role_manager_save")
        transition = route.index("if not was_team_member and is_team_member:")
        event = route.index("store_employment_event(", transition)

        self.assertLess(route.index("await member.add_roles("), transition)
        self.assertLess(transition, event)
        self.assertIn('source="team_role_assignment"', route)

    def test_team_since_prefers_explicit_value_then_event_then_legacy_applications(self):
        resolver = function_source(self.web, "team_since_for")

        explicit = resolver.index('entry.get("team_since")')
        event = resolver.index("employment_hired_at(entry, member.id)")
        legacy = resolver.index("for item in apps.values()")
        self.assertLess(explicit, event)
        self.assertLess(event, legacy)
        self.assertIn('{"hired", "accepted"}', resolver)

    def test_audit_log_returns_the_id_persisted_with_the_audit_row(self):
        audit = function_source(self.web, "log_audit")
        self.assertIn('audit_id = f"audit_{uuid.uuid4().hex[:6]}"', audit)
        self.assertIn('"id": audit_id', audit)
        self.assertIn("return audit_id", audit)

    def test_suite_status_update_does_not_create_an_employment_event(self):
        update = function_source(self.suite, "application_update")
        self.assertIn('"accepted"', update)
        self.assertNotIn("store_employment_event", update)
        self.assertNotIn("build_employment_event", update)


if __name__ == "__main__":
    unittest.main()
