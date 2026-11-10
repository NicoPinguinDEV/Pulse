"""Versioned, guild-scoped moderation reason templates and case references."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pulse_db
import pulse_mod_cases
import pulse_mod_reason_templates as templates


class VersionedReasonTemplateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.actor = SimpleNamespace(id=100, display_name="Leitung")

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def test_create_update_keeps_immutable_versions_and_resolves_exact_version(self):
        first = templates.create_template(
            1,
            "warn",
            "Beleidigung",
            "Wiederholte Beleidigungen im öffentlichen Chat.",
            self.actor.id,
            self.actor.display_name,
        )
        self.assertEqual(first["version"], 1)
        self.assertEqual(first["title"], "Beleidigung")

        second = templates.update_template(
            1,
            first["id"],
            "Wiederholte Beleidigung",
            "Wiederholte Beleidigungen trotz vorheriger Ermahnung im öffentlichen Chat.",
            self.actor.id,
            self.actor.display_name,
        )
        self.assertEqual(second["version"], 2)
        self.assertEqual(templates.get_template(1, first["id"], 1)["body"], "Wiederholte Beleidigungen im öffentlichen Chat.")
        self.assertEqual(templates.get_template(1, first["id"], 2)["body"], second["body"])

        token = templates.selection_token(first["id"], 1)
        reason, template_id, version = templates.resolve_reason(1, "warn", token)
        self.assertEqual(reason, "Wiederholte Beleidigungen im öffentlichen Chat.")
        self.assertEqual((template_id, version), (first["id"], 1))
        self.assertEqual(templates.resolve_reason(1, "warn", "Freie eigene Begründung."), ("Freie eigene Begründung.", None, None))

        versions = templates.list_versions(1, first["id"])
        self.assertEqual([item["version"] for item in versions], [2, 1])
        self.assertEqual(len(templates.list_templates(1, "warn")), 1)

    def test_templates_are_scoped_by_guild_and_action(self):
        first = templates.create_template(
            1, "ban", "Betrugsversuch", "Nachweisbarer Betrugsversuch im Spielbetrieb.",
            self.actor.id, self.actor.display_name,
        )
        self.assertIsNone(templates.get_template(2, first["id"]))
        with self.assertRaises(ValueError):
            templates.resolve_reason(1, "warn", templates.selection_token(first["id"], 1))
        self.assertEqual(templates.list_templates(1, "warn"), [])
        self.assertEqual(len(templates.list_templates(1, "ban")), 1)

    def test_archive_removes_template_from_autocomplete_but_preserves_history(self):
        first = templates.create_template(
            1, "timeout", "Spam", "Wiederholter Spam im öffentlichen Textkanal.",
            self.actor.id, self.actor.display_name,
        )
        self.assertTrue(templates.archive_template(1, first["id"], self.actor.id, "Leitung"))
        self.assertFalse(templates.archive_template(1, first["id"], self.actor.id, "Leitung"))
        self.assertEqual(templates.list_templates(1), [])
        self.assertEqual(templates.list_versions(1, first["id"])[0]["version"], 1)
        with self.assertRaises(ValueError):
            templates.resolve_reason(1, "timeout", templates.selection_token(first["id"], 1))
        with self.assertRaises(ValueError):
            templates.update_template(1, first["id"], "Neue Version", "Ein neuer Begründungstext.", 100, "Leitung")

    def test_invalid_template_input_and_tokens_fail_closed(self):
        with self.assertRaises(ValueError):
            templates.create_template(1, "unknown", "Titel", "Eine gültige Begründung.", 100, "Leitung")
        with self.assertRaises(ValueError):
            templates.create_template(1, "warn", "", "Eine gültige Begründung.", 100, "Leitung")
        with self.assertRaises(ValueError):
            templates.create_template(1, "warn", "Titel", "Kurz", 100, "Leitung")
        with self.assertRaises(ValueError):
            templates.resolve_reason(1, "warn", "@modtpl:kaputt")
        with self.assertRaises(ValueError):
            templates.resolve_reason(1, "warn", "@modtpl:MRT-ABCDEF12:999")

    def test_autocomplete_hides_templates_for_actions_without_both_permission_layers(self):
        import cogs.mod_cases as cog
        warn_template = templates.create_template(
            1, "warn", "Verwarnung", "Wiederholte Beleidigungen im öffentlichen Chat.",
            self.actor.id, self.actor.display_name,
        )
        ban_template = templates.create_template(
            1, "ban", "Ban", "Nachgewiesener Betrugsversuch im Spielbetrieb.",
            self.actor.id, self.actor.display_name,
        )
        interaction = SimpleNamespace(
            guild=SimpleNamespace(id=1),
            user=SimpleNamespace(
                id=self.actor.id,
                guild_permissions=SimpleNamespace(
                    moderate_members=True, kick_members=True, ban_members=True, administrator=False
                ),
            ),
        )
        pulse_rights = {"can_moderate_kicks": True, "can_moderate_bans": False}
        with patch.object(cog, "pulse_perms", return_value=pulse_rights):
            choices = cog._reason_template_choices(interaction, "warn", "")
            self.assertEqual([item.name.split(" · v")[0] for item in choices], [warn_template["title"]])
            self.assertEqual(
                cog._reason_template_choices(interaction, "ban", ""),
                [],
            )
        self.assertEqual(templates.list_templates(1, "ban")[0]["id"], ban_template["id"])

    def test_selected_version_is_stored_in_moderation_case_and_backup(self):
        template = templates.create_template(
            1, "warn", "Beleidigung", "Wiederholte Beleidigungen im öffentlichen Chat.",
            self.actor.id, self.actor.display_name,
        )
        reason, template_id, version = templates.resolve_reason(
            1, "warn", templates.selection_token(template["id"], 1)
        )
        target = SimpleNamespace(id=200, display_name="Bürger")
        case = pulse_mod_cases.create_case(
            1, target, "warn", reason, "", None, self.actor,
            reason_template_id=template_id,
            reason_template_version=version,
        )
        stored = pulse_mod_cases.get_case(case["id"])
        self.assertEqual(stored["reason"], reason)
        self.assertEqual(stored["reason_template_id"], template["id"])
        self.assertEqual(stored["reason_template_version"], 1)

        state = pulse_db.export_state()
        self.assertIn("mod_cases", state)
        self.assertEqual(len(state["mod_cases"]), 1)
        self.assertEqual(state["mod_cases"][0]["id"], case["id"])
        self.assertEqual(len(state["mod_reason_templates"]), 1)
        self.assertEqual(len(state["mod_reason_template_versions"]), 1)

        with pulse_db.connect() as db:
            db.execute("DELETE FROM mod_cases")
            db.execute("DELETE FROM mod_reason_templates")
        pulse_db.import_state(state)
        restored_case = pulse_mod_cases.get_case(case["id"])
        self.assertEqual(restored_case["reason_template_id"], template["id"])
        self.assertEqual(restored_case["reason_template_version"], 1)
        self.assertEqual(templates.get_template(1, template["id"], 1)["body"], reason)


if __name__ == "__main__":
    unittest.main()
