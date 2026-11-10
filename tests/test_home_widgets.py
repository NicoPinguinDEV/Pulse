"""Phase 19: personal dashboard widget selection defaults and validation."""
import unittest

from pulse_home import DEFAULT_HOME_WIDGETS, HOME_WIDGETS, normalize_home_widgets


class HomeWidgetTests(unittest.TestCase):
    def test_missing_preference_keeps_all_existing_widgets_visible(self):
        self.assertEqual(normalize_home_widgets(None), list(DEFAULT_HOME_WIDGETS))

    def test_selection_is_allowlisted_deduplicated_and_keeps_defined_order(self):
        values = ["inbox", "tasks", "unknown", "tasks", "week"]
        self.assertEqual(normalize_home_widgets(values), ["week", "tasks", "inbox"])

    def test_explicit_empty_selection_is_preserved(self):
        self.assertEqual(normalize_home_widgets([]), [])

    def test_malformed_preference_falls_back_to_full_layout(self):
        self.assertEqual(normalize_home_widgets("tickets"), list(DEFAULT_HOME_WIDGETS))

    def test_every_widget_key_is_unique(self):
        keys = [key for key, _ in HOME_WIDGETS]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertEqual(set(keys), set(DEFAULT_HOME_WIDGETS))


if __name__ == "__main__":
    unittest.main()
