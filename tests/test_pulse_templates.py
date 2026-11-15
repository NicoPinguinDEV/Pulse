"""Audit #123: central template library (versions, archive, rights, safe rendering, legacy import, backup)."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_app_comms
import pulse_db
import pulse_eventbus as bus
import pulse_policy as policy
import pulse_templates as tpl

G, G2 = "900000000000000001", "900000000000000002"
A1, A2 = "100000000000000001", "100000000000000002"
TICKET = {"can_manage_tickets": True}
APPS = {"can_manage_applications": True}
BOTH = {"can_manage_tickets": True, "can_manage_applications": True}
ADMIN = {"is_admin": True}
NOBODY = {"can_view_dashboard": True}


class LibraryCase(unittest.TestCase):
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

    def make(self, perms=TICKET, kind="ticket_reply", scope="*", title="Danke", body="Hallo {user}, danke für dein Ticket {ticket}."):
        return tpl.create(perms, G, kind, scope, title, body, A1, "Anna")


class CrudTests(LibraryCase):
    def test_create_get_and_audit_event(self):
        cursor = bus.poll(ADMIN)["cursor"]
        t = self.make()
        self.assertTrue(t["id"].startswith("TPL-"))
        self.assertEqual((t["version"], t["kind"], t["scope"], t["title"]), (1, "ticket_reply", "*", "Danke"))
        self.assertEqual(tpl.get(TICKET, G, t["id"])["body"], t["body"])
        events = bus.poll(ADMIN, cursor)["events"]
        self.assertEqual([(e["domain"], e["action"]) for e in events], [("system", "template_created")])

    def test_update_creates_new_version_and_keeps_history(self):
        t = self.make()
        u = tpl.update(TICKET, G, t["id"], "Danke", "Neuer Text für {user}.", A2, "Ben", expected_version=1)
        self.assertEqual((u["version"], u["body"]), (2, "Neuer Text für {user}."))
        old = tpl.get(TICKET, G, t["id"], version=1)
        self.assertEqual(old["body"], t["body"])
        self.assertFalse(old["is_current"])
        self.assertEqual([v["version"] for v in tpl.versions(TICKET, G, t["id"])], [2, 1])

    def test_concurrent_edit_is_detected(self):
        t = self.make()
        tpl.update(TICKET, G, t["id"], "Danke", "Von Anna.", A1, "Anna", expected_version=1)
        with self.assertRaises(tpl.TemplateConflict):
            tpl.update(TICKET, G, t["id"], "Danke", "Von Ben.", A2, "Ben", expected_version=1)
        self.assertEqual(tpl.get(TICKET, G, t["id"])["body"], "Von Anna.")

    def test_unchanged_update_and_missing_template(self):
        t = self.make()
        with self.assertRaises(ValueError):
            tpl.update(TICKET, G, t["id"], t["title"], t["body"], A1, "Anna")
        with self.assertRaises(ValueError):
            tpl.update(TICKET, G, "TPL-NOPE0000", "x", "y", A1, "Anna")

    def test_title_is_unique_among_active_templates_per_kind_and_scope(self):
        t = self.make(title="Danke")
        with self.assertRaises(tpl.TemplateConflict):
            self.make(title="danke")
        self.make(title="Danke", scope="Support")  # other scope is fine
        tpl.archive(TICKET, G, t["id"], A1, "Anna")
        self.make(title="Danke")  # archived titles are free again
        with self.assertRaises(tpl.TemplateConflict):
            tpl.restore(TICKET, G, t["id"], A1, "Anna")

    def test_archive_restore_and_listing(self):
        t = self.make()
        self.assertEqual(len(tpl.list_templates(TICKET, G)), 1)
        self.assertTrue(tpl.archive(TICKET, G, t["id"], A1, "Anna"))
        self.assertFalse(tpl.archive(TICKET, G, t["id"], A1, "Anna"))
        self.assertEqual(tpl.list_templates(TICKET, G), [])
        self.assertEqual(len(tpl.list_templates(TICKET, G, include_archived=True)), 1)
        with self.assertRaises(ValueError):
            tpl.update(TICKET, G, t["id"], "Neu", "Text", A1, "Anna")
        self.assertTrue(tpl.restore(TICKET, G, t["id"], A1, "Anna"))
        self.assertEqual(len(tpl.list_templates(TICKET, G)), 1)

    def test_other_guild_cannot_see_or_change(self):
        t = self.make()
        self.assertIsNone(tpl.get(TICKET, G2, t["id"]))
        self.assertEqual(tpl.list_templates(TICKET, G2), [])
        self.assertFalse(tpl.archive(TICKET, G2, t["id"], A1, "Anna"))
        with self.assertRaises(ValueError):
            tpl.update(TICKET, G2, t["id"], "x", "y", A1, "Anna")


class RightsTests(LibraryCase):
    def test_each_kind_needs_its_own_right(self):
        with self.assertRaises(policy.PermissionDenied):
            tpl.create(APPS, G, "ticket_reply", "*", "T", "x", A1, "Anna")
        with self.assertRaises(policy.PermissionDenied):
            tpl.create(NOBODY, G, "application_dm", "received", "T", "x", A1, "Anna")
        with self.assertRaises(policy.PermissionDenied):
            tpl.create(None, G, "ticket_reply", "*", "T", "x", A1, "Anna")
        self.assertEqual(pulse_db.export_state()["message_templates"], [])

    def test_read_update_archive_render_are_rights_checked(self):
        t = self.make()
        for call in (lambda: tpl.get(APPS, G, t["id"]),
                     lambda: tpl.update(APPS, G, t["id"], "N", "x", A1, "A"),
                     lambda: tpl.archive(APPS, G, t["id"], A1, "A"),
                     lambda: tpl.render(APPS, G, t["id"], {}),
                     lambda: tpl.versions(APPS, G, t["id"])):
            with self.assertRaises(policy.PermissionDenied):
                call()
        # a refused call must not have changed anything (not merely raised afterwards)
        current = tpl.get(TICKET, G, t["id"])
        self.assertEqual((current["version"], current["archived_at"], current["title"]), (1, None, t["title"]))
        self.assertEqual(len(tpl.versions(TICKET, G, t["id"])), 1)

    def test_listing_only_contains_kinds_the_person_may_use(self):
        self.make()
        tpl.create(APPS, G, "application_dm", "received", "Eingang", "Hallo {server}", A1, "Anna")
        self.assertEqual({t["kind"] for t in tpl.list_templates(BOTH, G)}, {"ticket_reply", "application_dm"})
        self.assertEqual({t["kind"] for t in tpl.list_templates(TICKET, G)}, {"ticket_reply"})
        self.assertEqual(tpl.list_templates(NOBODY, G), [])
        self.assertEqual({t["kind"] for t in tpl.list_templates(ADMIN, G, kind="application_dm")}, {"application_dm"})
        with self.assertRaises(ValueError):
            tpl.list_templates(ADMIN, G, kind="nope")

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            tpl.create(ADMIN, G, "nope", "*", "T", "x", A1, "Anna")


class ValidationTests(LibraryCase):
    def test_placeholder_rules(self):
        bad = ["Hallo {unbekannt}", "Hallo {user.name}", "Hallo {user:>99999}", "Hallo {user!r}", "Hallo {user", "Hallo {0}",
               "Hallo {__class__}", "Hallo {USER}"]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError):
                tpl.create(TICKET, G, "ticket_reply", "*", f"T{abs(hash(body))}", body, A1, "Anna")
        self.assertEqual(pulse_db.export_state()["message_templates"], [])

    def test_error_message_names_allowed_placeholders(self):
        with self.assertRaises(ValueError) as ctx:
            self.make(body="Hi {server}")
        self.assertIn("{user}", str(ctx.exception))

    def test_length_and_empty_checks(self):
        for title, body in (("", "x"), ("x" * 81, "x"), ("T", ""), ("T", "  "), ("T", "x" * 1801)):
            with self.subTest(title=title[:5], body=body[:5]), self.assertRaises(ValueError):
                tpl.create(TICKET, G, "ticket_reply", "*", title, body, A1, "Anna")
        with self.assertRaises(ValueError):
            tpl.create(TICKET, G, "ticket_reply", "*", "T", "x", "", "Anna")
        with self.assertRaises(ValueError):
            tpl.create(TICKET, "", "ticket_reply", "*", "T", "x", A1, "Anna")

    def test_update_is_validated_too(self):
        t = self.make()
        with self.assertRaises(ValueError):
            tpl.update(TICKET, G, t["id"], "Danke", "Hi {user.name}", A1, "Anna")
        self.assertEqual(tpl.get(TICKET, G, t["id"])["version"], 1)


class RenderTests(LibraryCase):
    def test_values_are_filled_and_missing_ones_become_dash(self):
        t = self.make()
        out = tpl.render(TICKET, G, t["id"], {"user": "Max"})
        self.assertEqual(out["text"], "Hallo Max, danke für dein Ticket –.")
        self.assertEqual((out["version"], out["kind"]), (1, "ticket_reply"))

    def test_mass_mentions_from_values_and_body_are_neutralised(self):
        t = self.make(body="{user} sagt @everyone und <@&123456789012345678>")
        text = tpl.render(TICKET, G, t["id"], {"user": "@here"})["text"]
        self.assertNotIn("@everyone", text)
        self.assertNotIn("@here", text)
        self.assertNotIn("<@&1", text)

    def test_values_for_other_kinds_are_ignored(self):
        t = self.make(body="Hallo {user}")
        self.assertEqual(tpl.render(TICKET, G, t["id"], {"user": "A", "server": "B", "__class__": "x"})["text"], "Hallo A")

    def test_format_tricks_in_values_are_not_interpreted(self):
        t = self.make(body="Hallo {user}")
        self.assertEqual(tpl.render(TICKET, G, t["id"], {"user": "{ticket} {0.__class__}"})["text"], "Hallo {ticket} {0.__class__}")

    def test_old_version_and_archived(self):
        t = self.make(body="v1 {user}")
        tpl.update(TICKET, G, t["id"], "Danke", "v2 {user}", A1, "Anna")
        self.assertEqual(tpl.render(TICKET, G, t["id"], {"user": "X"}, version=1)["text"], "v1 X")
        self.assertEqual(tpl.render(TICKET, G, t["id"], {"user": "X"})["text"], "v2 X")
        tpl.archive(TICKET, G, t["id"], A1, "Anna")
        with self.assertRaises(ValueError):
            tpl.render(TICKET, G, t["id"], {})
        with self.assertRaises(ValueError):
            tpl.render(TICKET, G, "TPL-NOPE0000", {})


class LegacyImportAndBackupTests(LibraryCase):
    def test_import_copies_customised_application_texts_and_ticket_replies_once(self):
        pulse_app_comms.save({"received": "Hallo! Bewerbung {id} bei {server} ist da."})
        pulse_db.set_setting("ticket_quick_replies", {"*": [{"title": "Gruß", "text": "Hi {user}"},
                                                            {"title": "Kaputt", "text": "Hi {user.name}"}]})
        first = tpl.import_legacy(BOTH, G, A1, "Anna")
        self.assertEqual(first["created"], len(pulse_app_comms.DEFAULTS) + 1)  # every application text (defaults + custom) + 1 valid reply
        self.assertEqual(len(first["skipped"]), 1)
        self.assertIn("Kaputt", first["skipped"][0])
        again = tpl.import_legacy(BOTH, G, A1, "Anna")
        self.assertEqual(again["created"], 0)
        received = [t for t in tpl.list_templates(BOTH, G, kind="application_dm") if t["scope"] == "received"][0]
        self.assertIn("Bewerbung {id}", received["body"])
        # legacy stores untouched
        self.assertEqual(pulse_db.get_setting("ticket_quick_replies")["*"][1]["title"], "Kaputt")

    def test_import_needs_the_right_of_every_requested_kind(self):
        with self.assertRaises(policy.PermissionDenied):
            tpl.import_legacy(TICKET, G, A1, "Anna")
        self.assertEqual(tpl.import_legacy(TICKET, G, A1, "Anna", kinds=("ticket_reply",))["created"], 0)

    def test_tables_are_part_of_the_backup_and_restore(self):
        t = self.make()
        tpl.update(TICKET, G, t["id"], "Danke", "Zweite Fassung {user}", A1, "Anna")
        state = pulse_db.export_state()
        self.assertEqual(len(state["message_templates"]), 1)
        self.assertEqual(len(state["message_template_versions"]), 2)
        with pulse_db.connect() as db:
            db.execute("DELETE FROM message_templates")
        self.assertIsNone(tpl.get(TICKET, G, t["id"]))
        pulse_db.import_state(state)
        self.assertEqual(tpl.get(TICKET, G, t["id"])["version"], 2)
        self.assertEqual(len(tpl.versions(TICKET, G, t["id"])), 2)


class LookupAndSyncTests(LibraryCase):
    """F8: consumer lookup (scopes, archived, no cap) and the sync used by the ticket tools page."""

    def test_lookup_filters_scopes_includes_archived_and_is_not_capped(self):
        for i in range(210):
            tpl.create(TICKET, G, "ticket_reply", "*", f"T{i:03d}", "x", A1, "Anna")
        self.assertEqual(len(tpl.lookup(TICKET, G, "ticket_reply")), 210)  # list_templates would cap at 200
        self.assertEqual(len(tpl.list_templates(TICKET, G, kind="ticket_reply", limit=1000)), 200)
        gone = tpl.lookup(TICKET, G, "ticket_reply", ["*"])[0]
        tpl.archive(TICKET, G, gone["id"], A1, "Anna")
        self.assertEqual(len(tpl.lookup(TICKET, G, "ticket_reply", ["*"])), 209)
        self.assertEqual(len(tpl.lookup(TICKET, G, "ticket_reply", ["*"], include_archived=True)), 210)
        self.assertEqual(tpl.lookup(TICKET, G, "ticket_reply", []), [])
        self.assertEqual(tpl.lookup(TICKET, G2, "ticket_reply"), [])
        with self.assertRaises(policy.PermissionDenied):
            tpl.lookup(APPS, G, "ticket_reply")

    def test_sync_kind_is_idempotent_and_marks_takeover_archives(self):
        entries = [("*", "A", "eins {user}"), ("support", "B", "zwei")]
        first = tpl.sync_kind(TICKET, G, "ticket_reply", entries, A1, "Anna")
        self.assertEqual((first["created"], first["updated"], first["archived"]), (2, 0, 0))
        self.assertEqual(tpl.sync_kind(TICKET, G, "ticket_reply", entries, A1, "Anna")["unchanged"], 2)
        broken = tpl.sync_kind(TICKET, G, "ticket_reply", [("*", "A", "kaputt {x.y}"), ("support", "B", "zwei")], A2, "Ben")
        self.assertEqual((broken["archived"], len(broken["skipped"])), (1, 1))
        archived = [r for r in tpl.lookup(TICKET, G, "ticket_reply", include_archived=True) if r["archived_at"]]
        self.assertEqual([r["archived_by_id"] for r in archived], [tpl.TAKEOVER_ID])
        removed = tpl.sync_kind(TICKET, G, "ticket_reply", [("support", "B", "zwei")], A2, "Ben")
        self.assertEqual(removed["archived"], 0)  # "A" is already archived; nothing left to remove
        self.assertEqual(tpl.sync_kind(TICKET, G, "ticket_reply", [], A2, "Ben")["archived"], 1)
        plain = [r for r in tpl.lookup(TICKET, G, "ticket_reply", ["support"], include_archived=True)][0]
        self.assertEqual(plain["archived_by_id"], A2)  # a normal removal keeps the person as archiver

    def test_sync_kind_needs_the_right_and_reports_conflicts(self):
        with self.assertRaises(policy.PermissionDenied):
            tpl.sync_kind(APPS, G, "ticket_reply", [("*", "A", "x")], A1, "Anna")
        tpl.sync_kind(TICKET, G, "ticket_reply", [("*", "A", "eins")], A1, "Anna")
        row = tpl.lookup(TICKET, G, "ticket_reply")[0]
        tpl.update(TICKET, G, row["id"], "A", "dazwischen", A2, "Ben")
        stale = {**row}  # the sync read the old version before Ben saved
        with patch.object(tpl, "lookup", return_value=[stale]):
            result = tpl.sync_kind(TICKET, G, "ticket_reply", [("*", "A", "zwei")], A1, "Anna")
        self.assertEqual(result["updated"], 0)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertEqual(tpl.lookup(TICKET, G, "ticket_reply")[0]["body"], "dazwischen")


if __name__ == "__main__":
    unittest.main()
