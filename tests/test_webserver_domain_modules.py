"""Regression tests for the modular webserver composition root."""

import unittest
from pathlib import Path

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

    def test_composition_root_stays_below_four_thousand_lines(self):
        line_count = len(Path(webserver.__file__).read_text(encoding="utf-8").splitlines())
        self.assertLessEqual(line_count, 4_000)

    def test_each_domain_module_stays_below_twenty_five_hundred_lines(self):
        for module in webserver._PULSE_WEB_MODULES:
            with self.subTest(module=module.__name__):
                line_count = len(Path(module.__file__).read_text(encoding="utf-8").splitlines())
                self.assertLessEqual(line_count, 2_500)

    def test_domain_handlers_resolve_globals_from_the_live_composition_root(self):
        representatives = {
            "home": "pulse_web.dashboard",
            "team_activity_api": "pulse_web.tickets",
            "team_list_page": "pulse_web.team",
            "applications_page": "pulse_web.applications",
            "backups_page": "pulse_web.backups",
            "settings_page": "pulse_web.administration",
        }
        for name, module_name in representatives.items():
            handler = getattr(webserver, name)
            with self.subTest(handler=name):
                self.assertEqual(handler.__module__, module_name)
                self.assertIs(handler.__globals__, vars(webserver))

    def test_domain_modules_share_the_existing_fastapi_application(self):
        for module in webserver._PULSE_WEB_MODULES:
            with self.subTest(module=module.__name__):
                self.assertIs(module.app, webserver.app)


if __name__ == "__main__":
    unittest.main()
