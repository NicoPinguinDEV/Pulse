"""Regression tests for the modular webserver composition root."""

import unittest

import webserver


EXPECTED_MODULES = (
    "pulse_web.ui",
    "pulse_web.dashboard",
    "pulse_web.tickets",
    "pulse_web.team",
    "pulse_web.applications",
    "pulse_web.backups",
    "pulse_web.administration",
)

EXPECTED_LEGACY_EXPORTS = (
    "home",
    "dashboard_main",
    "pulse_tickets_dashboard",
    "team_list_page",
    "applications_page",
    "backups_page",
    "settings_page",
)


class WebserverDomainModuleTests(unittest.TestCase):
    def test_domain_modules_load_in_the_previous_registration_order(self):
        actual = tuple(module.__name__ for module in webserver._PULSE_WEB_MODULES)
        self.assertEqual(actual, EXPECTED_MODULES)

    def test_legacy_webserver_exports_remain_available(self):
        for name in EXPECTED_LEGACY_EXPORTS:
            with self.subTest(name=name):
                self.assertTrue(callable(getattr(webserver, name, None)))

    def test_domain_modules_share_the_existing_fastapi_application(self):
        for module in webserver._PULSE_WEB_MODULES:
            with self.subTest(module=module.__name__):
                self.assertIs(module.app, webserver.app)


if __name__ == "__main__":
    unittest.main()
