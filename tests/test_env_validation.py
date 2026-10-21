import unittest

from pulse_env_validation import validate_environment


class EnvironmentValidationTests(unittest.TestCase):
    def test_valid_optional_settings_are_quiet(self):
        env = {
            "SERVER_PORT": "25095",
            "MAX_SHIFT_HOURS": "12",
            "PULSE_AI_MODERATION_CONFIDENCE": "0.72",
            "PULSE_RSSHUB_FALLBACK_URLS": "https://mirror.example,https://second.example",
            "PUBLIC_BASE_URL": "https://pulse.example",
            "PULSE_SUPPORT_TEAM_ROLE_IDS": "12345,67890",
        }
        self.assertEqual(validate_environment(env), [])

    def test_invalid_ranges_report_names_without_echoing_values(self):
        secret_like_bad_value = "sensitive-invalid-setting-value"
        warnings = validate_environment({
            "SERVER_PORT": secret_like_bad_value,
            "MAX_SHIFT_HOURS": "999",
            "PUBLIC_BASE_URL": "not-a-url",
        })
        rendered = "\n".join(warnings)
        self.assertIn("SERVER_PORT", rendered)
        self.assertIn("MAX_SHIFT_HOURS", rendered)
        self.assertIn("PUBLIC_BASE_URL", rendered)
        self.assertNotIn(secret_like_bad_value, rendered)

    def test_empty_optional_values_are_not_errors(self):
        self.assertEqual(validate_environment({
            "PULSE_REPORT_CHANNEL_ID": "",
            "PULSE_RSSHUB_BASE_URL": "",
            "PULSE_SUPPORT_TEAM_ROLE_IDS": "",
        }), [])

    def test_example_domain_is_flagged_without_printing_url_value(self):
        raw = "https://your-domain.example"
        warnings = validate_environment({"PUBLIC_BASE_URL": raw})
        self.assertTrue(any("PUBLIC_BASE_URL" in warning for warning in warnings))
        self.assertTrue(all(raw not in warning for warning in warnings))

    def test_url_list_rejects_invalid_member_without_echoing_it(self):
        raw = "https://mirror.example,not-a-url"
        warnings = validate_environment({"PULSE_RSSHUB_FALLBACK_URLS": raw})
        self.assertEqual(len(warnings), 1)
        self.assertIn("PULSE_RSSHUB_FALLBACK_URLS", warnings[0])
        self.assertNotIn("not-a-url", warnings[0])


if __name__ == "__main__":
    unittest.main()
