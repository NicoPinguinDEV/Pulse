"""Audit #123: Verwaltung der zentralen Vorlagenbibliothek im Dashboard (Rechte je Art, Versionen, Archiv, Ankündigungs-Auswahl; temp DB)."""
import unittest

import pulse_db
import pulse_templates as templates
from tests.fakes import FakeMember
from tests.test_wiki_plus import Base as WikiBase

ANNOUNCER = {"can_view_dashboard": True, "can_manage_announcements": True}
TICKETER = {"can_view_dashboard": True, "can_manage_tickets": True}
TEAM = {"can_view_dashboard": True}
ADMIN = {"can_view_dashboard": True, "is_admin": True}


class PageBase(WikiBase):
    def post(self, path, **data):
        return self.client.post(path, data=data, follow_redirects=False)

    def make(self, perms, kind="announcement", title="Neue Regel", body="Hallo {name}, bitte lesen.", scope=""):
        return templates.create(perms, self.guild_id, kind, scope, title, body, "1", "Ann")

    def setUp(self):
        super().setUp()
        self.guild_id = 1                      # FakeGuild.id
        self.members.append(FakeMember(1, "Ann", roles=[self.role]))


class AccessTests(PageBase):
    def test_each_person_sees_only_the_kinds_they_may_manage(self):
        self.make(ANNOUNCER)
        self.make(TICKETER, kind="ticket_reply", title="Begrüßung", body="Hallo {user}", scope="Support")
        with self.as_user(ANNOUNCER):
            html = self.client.get("/vorlagen").text
        self.assertIn("Neue Regel", html)
        self.assertNotIn("Begrüßung", html)
        with self.as_user(TICKETER):
            html = self.client.get("/vorlagen").text
        self.assertIn("Begrüßung", html)
        self.assertNotIn("Neue Regel", html)
        with self.as_user(TEAM):
            self.assertIn("keine Vorlagenart freigegeben", self.client.get("/vorlagen").text)

    def test_other_kinds_are_hidden_and_not_writable(self):
        created = self.make(ANNOUNCER)
        with self.as_user(TICKETER):
            self.assertEqual(self.client.get(f"/vorlagen/{created['id']}").status_code, 404)
            self.assertEqual(self.post(f"/vorlagen/{created['id']}/speichern", title="X", body="Y").status_code, 403)
            self.assertEqual(self.post(f"/vorlagen/{created['id']}/archivieren").status_code, 403)
            self.assertEqual(self.post("/vorlagen/neu", kind="announcement", title="X", body="Y").status_code, 403)
        self.assertEqual(templates.get(ANNOUNCER, self.guild_id, created["id"])["version"], 1)
        self.assertFalse(templates.get(ANNOUNCER, self.guild_id, created["id"]).get("archived_at"))


class FlowTests(PageBase):
    def test_create_edit_version_conflict_archive_and_restore(self):
        with self.as_user(ANNOUNCER):
            self.assertEqual(self.post("/vorlagen/neu", kind="announcement", title="Wartung", body="Wartung am {datum}.", scope="").status_code, 303)
            [t] = templates.list_templates(ANNOUNCER, self.guild_id)
            self.assertEqual((t["title"], t["current_version"]), ("Wartung", 1))
            self.assertEqual(self.post(f"/vorlagen/{t['id']}/speichern", title="Wartung", body="Wartung am {datum} ab 20 Uhr.", erwartet="1").status_code, 303)
            self.assertEqual(templates.get(ANNOUNCER, self.guild_id, t["id"])["version"], 2)
            stale = self.post(f"/vorlagen/{t['id']}/speichern", title="Wartung", body="Anderer Text", erwartet="1")
            self.assertIn("err", stale.headers["location"])
            self.assertEqual(templates.get(ANNOUNCER, self.guild_id, t["id"])["version"], 2)
            page = self.client.get(f"/vorlagen/{t['id']}").text
            self.assertIn("Version 2", page)
            self.assertIn("Version 1", page)
            self.assertEqual(self.post(f"/vorlagen/{t['id']}/archivieren").status_code, 303)
            self.assertNotIn("Wartung", self.client.get("/vorlagen").text.split("Bestehende Texte")[0].split("Ankündigung")[1])
            self.assertIn("Wartung", self.client.get("/vorlagen?archiv=1").text)
            self.assertEqual(self.post(f"/vorlagen/{t['id']}/wiederherstellen").status_code, 303)
            self.assertEqual(len(templates.list_templates(ANNOUNCER, self.guild_id)), 1)

    def test_invalid_input_is_refused_with_a_message_and_writes_nothing(self):
        with self.as_user(ANNOUNCER):
            for data in ({"title": "T", "body": "Hallo {unbekannt}"}, {"title": "", "body": "x"}, {"title": "T", "body": ""}, {"title": "T", "body": "x" * 5000}):
                resp = self.post("/vorlagen/neu", kind="announcement", **data)
                self.assertTrue(resp.status_code == 422 or "err" in resp.headers.get("location", ""), str(data))
            self.assertEqual(self.post("/vorlagen/neu", kind="gibtesnicht", title="T", body="x").status_code, 403)     # unbekannte Art = keine Berechtigung
            self.assertEqual(templates.list_templates(ANNOUNCER, self.guild_id), [])
            self.assertEqual(self.post("/vorlagen/TPL-NOPE/speichern", title="T", body="x").status_code, 404)
            self.assertEqual(self.client.get("/vorlagen/TPL-NOPE").status_code, 404)

    def test_duplicate_titles_and_escaping(self):
        with self.as_user(ANNOUNCER):
            self.post("/vorlagen/neu", kind="announcement", title="<b>Titel</b>", body="Text", scope="")
            duplicate = self.post("/vorlagen/neu", kind="announcement", title="<b>Titel</b>", body="Anderer Text", scope="")
            self.assertIn("err", duplicate.headers["location"])
            html = self.client.get("/vorlagen").text
        self.assertNotIn("<b>Titel</b>", html)
        self.assertEqual(len(templates.list_templates(ANNOUNCER, self.guild_id)), 1)


class IntegrationTests(PageBase):
    def test_announcement_form_offers_the_library_templates(self):
        self.make(ANNOUNCER, title="Wartung", body="Wartung am {datum}")
        with self.as_user(ANNOUNCER):
            html = self.client.get("/announcements").text
        self.assertIn("Vorlage verwenden", html)
        self.assertIn('data-body="Wartung am {datum}"', html)
        self.assertIn("/vorlagen", html)

    def test_without_templates_the_form_is_unchanged(self):
        with self.as_user(ANNOUNCER):
            html = self.client.get("/announcements").text
        self.assertNotIn("Vorlage verwenden", html)

    def test_admin_import_copies_legacy_texts_once(self):
        with self.as_user(TICKETER):
            self.assertEqual(self.post("/vorlagen/import").status_code, 403)
        with self.as_user(ADMIN):
            self.assertEqual(self.post("/vorlagen/import").status_code, 303)
            self.assertEqual(self.post("/vorlagen/import").status_code, 303)       # idempotent
        kinds = {t["kind"] for t in templates.list_templates(ADMIN, self.guild_id)}
        self.assertTrue(kinds <= {"application_dm", "ticket_reply"})

    def test_backup_tables_and_help(self):
        import pulse_help
        self.assertIn("message_templates", pulse_db.TABLES)
        self.assertIn("templates", pulse_help.TOPICS)


if __name__ == "__main__":
    unittest.main()
