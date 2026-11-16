"""Applicant-DM consumers use the central versioned library with explicit legacy/builtin fallbacks."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_app_comms as comms
import pulse_db
import pulse_templates as templates

GUILD = "900000000000000001"
ACTOR = "100000000000000001"
APPS = {"can_manage_applications": True}


class ApplicantLibraryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.tmp.name) / "pulse.db")
        self.patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.patch.start()
        pulse_db._SCHEMA_READY.discard(self.db_path)
        pulse_db.init_db()

    def tearDown(self):
        pulse_db._SCHEMA_READY.discard(self.db_path)
        self.patch.stop()
        self.tmp.cleanup()


class ApplicantLibraryResolutionTests(ApplicantLibraryCase):
    def test_active_library_version_is_used_and_journal_reference_is_versioned(self):
        entry = templates.create(APPS, GUILD, "application_dm", "received", "Eingang",
                                 "Hallo {server}, Bewerbung {id}: {rolle}", ACTOR, "Admin")
        resolved = comms.resolve_for_guild("received", GUILD, APPS, server="Bochum", id="A-12", rolle="@everyone")
        self.assertEqual(resolved["source"], "library")
        self.assertEqual(resolved["text"], "Hallo Bochum, Bewerbung A-12: @\u200beveryone")
        self.assertEqual(resolved["template_ref"], f'{entry["id"]}:v1')

    def test_missing_library_entry_uses_legacy_store(self):
        comms.save({"received": "Alttext für {server}: {id}"}, actor="Admin")
        resolved = comms.resolve_for_guild("received", GUILD, APPS, server="Bochum", id="A-12")
        self.assertEqual(resolved, {"text": "Alttext für Bochum: A-12", "template_ref": "legacy:received", "source": "legacy"})

    def test_legacy_editor_sync_creates_and_updates_library_versions(self):
        comms.save({"received": "Erste Fassung {server} {id}"}, actor="Admin")
        first = comms.sync_library(GUILD, APPS, ACTOR, "Admin")
        self.assertEqual(first["created"], len(comms.DEFAULTS))
        first_resolved = comms.resolve_for_guild("received", GUILD, APPS, server="Bochum", id="A-1")
        self.assertEqual(first_resolved["text"], "Erste Fassung Bochum A-1")
        self.assertEqual(first_resolved["source"], "library")

        comms.save({"received": "Zweite Fassung {server} {id}"}, actor="Admin")
        second = comms.sync_library(GUILD, APPS, ACTOR, "Admin")
        self.assertEqual(second["updated"], 1)
        updated = comms.resolve_for_guild("received", GUILD, APPS, server="Bochum", id="A-2")
        self.assertEqual(updated["text"], "Zweite Fassung Bochum A-2")
        self.assertTrue(updated["template_ref"].endswith(":v2"))
        self.assertEqual(len(templates.lookup(APPS, GUILD, "application_dm", ["received"])), 1)

    def test_manual_archive_does_not_resurrect_legacy_custom_text(self):
        comms.save({"received": "Alte Sonderfassung {server}"}, actor="Admin")
        entry = templates.create(APPS, GUILD, "application_dm", "received", "Eingang",
                                 "Zentrale Sonderfassung {server}", ACTOR, "Admin")
        templates.archive(APPS, GUILD, entry["id"], ACTOR, "Admin")
        resolved = comms.resolve_for_guild("received", GUILD, APPS, server="Bochum")
        self.assertEqual(resolved["source"], "builtin")
        self.assertEqual(resolved["text"], comms.DEFAULTS["received"].replace("{server}", "Bochum"))
        self.assertNotIn("Alte Sonderfassung", resolved["text"])

    def test_legacy_takeover_archive_keeps_legacy_fallback(self):
        comms.save({"received": "Legacy-Übernahme {server} {id}"}, actor="Admin")
        entry = templates.create(APPS, GUILD, "application_dm", "received", "Eingang",
                                 "Library-Text {server} {id}", ACTOR, "Admin")
        sync = templates.sync_kind(APPS, GUILD, "application_dm",
                                   [("received", "Eingang", "Ungültig {unknown.path}")], ACTOR, "Admin")
        self.assertEqual(sync["archived"], 1)
        archived = templates.lookup(APPS, GUILD, "application_dm", ["received"], include_archived=True)
        self.assertEqual(archived[0]["archived_by_id"], templates.TAKEOVER_ID)
        resolved = comms.resolve_for_guild("received", GUILD, APPS, server="Bochum", id="A-3")
        self.assertEqual(resolved["source"], "legacy")
        self.assertEqual(resolved["text"], "Legacy-Übernahme Bochum A-3")

    def test_legacy_editor_displays_active_library_text(self):
        comms.save({"received": "Legacy-Text {server}"}, actor="Admin")
        templates.create(APPS, GUILD, "application_dm", "received", "Eingang",
                         "Library-Text {server}", ACTOR, "Admin")
        current = comms.load_for_guild(GUILD, APPS)
        self.assertEqual(current["received"], "Library-Text {server}")


if __name__ == "__main__":
    unittest.main()
