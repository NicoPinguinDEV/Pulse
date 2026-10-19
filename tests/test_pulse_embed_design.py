import unittest
from unittest.mock import patch

import pulse_embed_design as d


class BrandingTests(unittest.TestCase):
    def setUp(self):
        d.invalidate_cache()

    def tearDown(self):
        d.invalidate_cache()

    def test_defaults_and_invalid_values_fall_back(self):
        brand = d.normalize_branding({"brand_name": "  ", "colors": {"success": "nope", "danger": "#abcdef"}})
        self.assertEqual(brand["brand_name"], "Pulse TeamOS")
        self.assertEqual(brand["colors"]["success"], d.DEFAULT_COLORS["success"])
        self.assertEqual(brand["colors"]["danger"], "#ABCDEF")
        self.assertEqual(d.normalize_branding(None)["colors"], d.DEFAULT_COLORS)

    def test_semantic_colors(self):
        brand = d.normalize_branding({})
        self.assertEqual(d.color_int("success", brand), 0x12B76A)
        self.assertEqual(d.color_int("error", brand), 0xF04438)
        self.assertEqual(d.color_int("warn", brand), 0xF79009)
        self.assertEqual(d.color_int("audit", brand), 0x667085)
        self.assertEqual(d.color_int("info", brand), 0x5B5CF0)
        self.assertEqual(d.color_int("unbekannt", brand), 0x5B5CF0)

    def test_branding_survives_missing_database(self):
        with patch("pulse_db.get_setting", side_effect=RuntimeError("db down")):
            self.assertEqual(d.branding()["footer_text"], d.DEFAULT_BRANDING["footer_text"])


class LimitAndSafetyTests(unittest.TestCase):
    def test_spec_respects_discord_limits(self):
        brand = d.normalize_branding({})
        spec = d.build_embed_spec("info", title="T" * 400, description="D" * 5000,
                                  fields=[("N" * 300, "V" * 2000)] * 30, footer="F" * 3000, brand=brand)
        self.assertEqual(len(spec["title"]), 256)
        self.assertLessEqual(len(spec["description"]), 4096)
        self.assertLessEqual(len(spec["fields"]), 25)
        self.assertTrue(all(len(n) <= 256 and len(v) <= 1024 for n, v, _ in spec["fields"]))
        total = len(spec["title"]) + len(spec["description"]) + len(spec["footer"]) + sum(len(n) + len(v) for n, v, _ in spec["fields"])
        self.assertLessEqual(total, 6000)

    def test_empty_field_values_get_fallback(self):
        spec = d.build_embed_spec("info", fields=[("", "")], brand=d.normalize_branding({}))
        self.assertEqual(spec["fields"][0][1], "–")

    def test_image_urls_are_validated(self):
        self.assertEqual(d.safe_image_url("http://example.com/a.png"), "")
        self.assertEqual(d.safe_image_url("javascript:alert(1)"), "")
        self.assertEqual(d.safe_image_url("https://cdn.example.com/a.png"), "https://cdn.example.com/a.png")
        self.assertEqual(d.safe_image_url("attachment://welcome_banner_1a2b3c4d.png"), "attachment://welcome_banner_1a2b3c4d.png")
        self.assertEqual(d.safe_image_url("attachment://../x"), "")
        spec = d.build_embed_spec("info", thumbnail_url="", image_url="ftp://x", brand=d.normalize_branding({}))
        self.assertEqual((spec["thumbnail_url"], spec["image_url"]), ("", ""))

    def test_mass_mentions_are_neutralized(self):
        text = d.neutralize_mentions("Hallo @everyone und @here <@&123> <@456>")
        self.assertNotIn("@everyone", text)
        self.assertNotIn("@here", text)
        self.assertNotIn("<@&123>", text)
        self.assertIn("<@456>", text)

    def test_templates(self):
        self.assertEqual(d.validate_template("Hi {display_name}", {"display_name"}), [])
        self.assertEqual(d.validate_template("Hi {foo}", {"display_name"}), ["Unbekannter Platzhalter {foo}."])
        self.assertTrue(d.validate_template("Hi {display_name", {"display_name"}))
        self.assertTrue(d.validate_template("{display_name!r}", {"display_name"}))
        self.assertEqual(d.render_template("{a} / {b}", {"a": "X", "b": None}), "X / –")
        self.assertEqual(d.render_template("{a} {unknown}", {"a": "X"}), "X –")

    def test_discord_embed_conversion(self):
        embed = d.make_embed("success", title="Fertig", description="Alles gut", fields=[("Wer", "Max", True)],
                             thumbnail_url="https://a.example/x.png", image_url="https://a.example/b.png")
        self.assertEqual(embed.colour.value, d.color_int("success"))
        self.assertEqual(embed.thumbnail.url, "https://a.example/x.png")
        self.assertEqual(embed.image.url, "https://a.example/b.png")
        self.assertEqual(embed.fields[0].name, "Wer")
        self.assertTrue(embed.footer.text)


if __name__ == "__main__":
    unittest.main()
