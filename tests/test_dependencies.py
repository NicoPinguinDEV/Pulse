"""K-13: dependency floors and time zone handling."""
import re
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class RequirementsTests(unittest.TestCase):
    def requirements(self):
        return (ROOT / "requirements.txt").read_text(encoding="utf-8")

    def test_discord_floor_supports_components_v2(self):
        match = re.search(r"^discord\.py>=(\d+)\.(\d+)", self.requirements(), re.M)
        self.assertIsNotNone(match)
        self.assertGreaterEqual((int(match.group(1)), int(match.group(2))), (2, 6))
        from discord import ui
        for name in ("LayoutView", "Container", "Section", "TextDisplay", "MediaGallery"):
            self.assertTrue(hasattr(ui, name), name)

    def test_tzdata_is_required_and_berlin_loads(self):
        self.assertRegex(self.requirements(), r"(?m)^tzdata\b")
        from zoneinfo import ZoneInfo
        self.assertEqual(str(ZoneInfo("Europe/Berlin")), "Europe/Berlin")


class TimezoneFallbackTests(unittest.TestCase):
    def test_missing_zone_data_falls_back_loudly(self):
        import webserver
        with patch.object(webserver, "ZoneInfo", side_effect=Exception("no tz data")), patch("builtins.print") as printed:
            self.assertIsNone(webserver._load_panel_timezone())
        self.assertIn("tzdata", printed.call_args[0][0])

    def test_configured_zone_is_used(self):
        import webserver
        with patch.dict("os.environ", {"PANEL_TIMEZONE": "Europe/Berlin"}):
            self.assertEqual(str(webserver._load_panel_timezone()), "Europe/Berlin")


if __name__ == "__main__":
    unittest.main()
