"""Tests for Pulse's API-free old-announcement search and explanation."""
from __future__ import annotations

import unittest

from pulse_announcement_utils import (
    build_announcement_explanation,
    clean_announcement_text,
    score_announcement,
)


class AnnouncementArchiveUtilityTests(unittest.TestCase):
    def test_german_vehicle_colour_terms_find_the_announcement(self):
        content = (
            "Fahrzeugfarben\n"
            "Bitte beachtet die Vorgaben für die Farben eurer Autos.\n"
            "- Erlaubt sind Schwarz, Weiß und Dunkelblau.\n"
            "- Sonderfarben müssen vorher genehmigt werden."
        )
        self.assertGreater(score_announcement("Farben des Autos", content), 0)
        self.assertEqual(score_announcement("Bewerbungsfrist Medic", content), 0)

    def test_explanation_keeps_the_original_details_and_adds_no_new_colour(self):
        content = (
            "Fahrzeugfarben\n"
            "Bitte beachtet die Vorgaben für die Farben eurer Autos.\n"
            "- Erlaubt sind Schwarz, Weiß und Dunkelblau.\n"
            "- Sonderfarben müssen vorher genehmigt werden."
        )
        explanation = build_announcement_explanation("Fahrzeugfarben", content)
        self.assertIn("Schwarz, Weiß und Dunkelblau", explanation)
        self.assertIn("Sonderfarben müssen vorher genehmigt werden", explanation)
        self.assertIn("Original-Ankündigung", explanation)
        self.assertNotIn("Rot", explanation)

    def test_markdown_cleanup_keeps_bullets_readable(self):
        cleaned = clean_announcement_text(
            "# **Hinweis**\n> Bitte beachtet die Vorgabe.\n- **Schwarz** ist erlaubt."
        )
        self.assertIn("Hinweis", cleaned)
        self.assertIn("Bitte beachtet die Vorgabe.", cleaned)
        self.assertIn("• Schwarz ist erlaubt.", cleaned)

    def test_empty_or_generic_query_does_not_match_everything(self):
        self.assertEqual(score_announcement("", "Fahrzeugfarben sind geregelt."), 0)
        self.assertEqual(score_announcement("und das die", "Fahrzeugfarben sind geregelt."), 0)


if __name__ == "__main__":
    unittest.main()
