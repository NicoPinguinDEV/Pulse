"""Phase 19: personal dashboard widget selection defaults and validation."""
import unittest

from pulse_home import (
    DEFAULT_HOME_WIDGETS,
    HOME_WIDGETS,
    MAX_DASHBOARD_FAVORITES,
    normalize_dashboard_favorites,
    normalize_home_widgets,
)


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


class DashboardFavoriteNormalizationTests(unittest.TestCase):
    def test_missing_or_malformed_favorites_are_empty(self):
        destinations = {"/me": "Mein Pulse", "/tickets": "Tickets"}
        self.assertEqual(normalize_dashboard_favorites(None, destinations), [])
        self.assertEqual(normalize_dashboard_favorites("tickets", destinations), [])

    def test_favorites_are_allowlisted_deduplicated_and_keep_user_order(self):
        destinations = {
            "/me": "Mein Pulse",
            "/tickets": "Tickets",
            "//evil.example": "External",
            "https://evil.example": "External",
            "/\\\\evil": "Malformed",
        }
        stored = ["/tickets", "//evil.example", "/me", "/tickets", "https://evil.example", "/missing"]
        self.assertEqual(normalize_dashboard_favorites(stored, destinations), ["/tickets", "/me"])

    def test_favorites_are_bounded(self):
        destinations = {f"/page-{index}": f"Seite {index}" for index in range(MAX_DASHBOARD_FAVORITES + 5)}
        selected = list(destinations)
        self.assertEqual(
            normalize_dashboard_favorites(selected, destinations),
            selected[:MAX_DASHBOARD_FAVORITES],
        )


if __name__ == "__main__":
    unittest.main()
