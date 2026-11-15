"""Audit #68: Kontexthilfe an den neueren komplexen Formularen (Bestätigungen, Besprechungen, Quiz, Automationen, Bereich, Soll-Rechte, Testmodus)."""
import unittest

import pulse_db
import pulse_help
from tests import test_automation_center as automation_base
from tests.test_wiki_plus import MANAGER as WIKI_MANAGER
from tests.test_wiki_plus import Base as WikiBase

NEW_TOPICS = ("ack", "meeting", "quiz", "rights_target", "realm", "automation_event", "automation_depends", "testmode")
LEAD = {"can_view_dashboard": True, "can_promote": True, "can_manage_tasks": True}
TRAINER = {"can_view_dashboard": True, "can_manage_training": True}


class TopicTests(unittest.TestCase):
    def test_every_new_topic_has_an_accessible_text(self):
        for key in NEW_TOPICS:
            html = pulse_help.tip(key)
            self.assertIn("<details", html, key)
            self.assertIn('aria-label="Hilfe anzeigen"', html, key)
            self.assertGreater(len(pulse_help.TOPICS[key]), 40, key)

    def test_unknown_topics_render_nothing(self):
        self.assertEqual(pulse_help.tip("gibtesnicht"), "")


class PageTests(WikiBase):
    def test_pages_show_their_help(self):
        qid = pulse_db.create_training("Funk", "", 60, 20, "9", "Lead", [{"question": "F?", "options": ["A", "B"], "answer_index": 0}])
        with self.as_user(WIKI_MANAGER):
            self.assertIn("pulse-help", self.client.get("/bestaetigungen").text)
        with self.as_user(LEAD):
            self.assertIn("pulse-help", self.client.get("/besprechungen").text)
        with self.as_user(TRAINER):
            self.assertIn("pulse-help", self.client.get(f"/training/{qid}/verwaltung").text)


class EditorTests(automation_base.AutomationRouteTests):
    def test_editor_and_case_form_show_their_help(self):
        html = self.client.get("/ultimate/automations").text
        self.assertGreaterEqual(html.count("pulse-help"), 2)
        self.assertIn("Die Automation erhält keine Inhalte", html)
        self.assertIn("Testläufe zählen nicht", html)
        self.assertIn("Ein Vorgang gehört genau zu einem Bereich", self.client.get("/ultimate/cases").text)


for _name in [n for n in dir(automation_base.AutomationRouteTests) if n.startswith("test_") and n not in EditorTests.__dict__]:
    setattr(EditorTests, _name, None)


if __name__ == "__main__":
    unittest.main()
