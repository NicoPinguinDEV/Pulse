"""Application #104: exact Discord-ID duplicate hints are read-only and reviewer-only."""
import ast
import copy
import unittest
from pathlib import Path

import pulse_application_duplicates as duplicates

ROOT = Path(__file__).resolve().parents[1]


def function_source(path, name):
    source = (ROOT / path).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(source, node) or ""
    raise AssertionError(f"Function {name!r} not found in {path}")


class DuplicateMatcherTests(unittest.TestCase):
    def setUp(self):
        self.apps = {
            "APP-001": {
                "user_id": "12345678901234567",
                "name": "Bewerber Eins",
                "status": "pending",
                "created_at": "2026-11-01T10:00:00+01:00",
                "text": "Private Motivation A",
            },
            "APP-002": {
                "discord_id": "12345678901234567",
                "name": "Anderer Anzeigename",
                "status": "rejected",
                "created_at": "2026-11-03T10:00:00+01:00",
                "text": "Private Motivation B",
                "reviews": {"900": {"notes": "Interne Recruiter-Notiz"}},
            },
            "APP-003": {
                "user_id": "22345678901234567",
                "name": "Bewerber Eins",
                "status": "interview",
                "created_at": "2026-11-04T10:00:00+01:00",
            },
            "APP-004": {
                "user_id": "not-a-discord-id",
                "discord_id": "not-a-discord-id",
                "name": "Ungültige ID",
                "status": "pending",
            },
        }

    def test_matches_only_exact_discord_account_not_name_or_fuzzy_fields(self):
        matches = duplicates.find_duplicate_applications(self.apps, "APP-001")
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0], {
            "application_id": "APP-002",
            "status": "rejected",
            "created_at": "2026-11-03T10:00:00+01:00",
        })

    def test_supports_legacy_applicant_id_but_rejects_malformed_discord_ids(self):
        apps = {
            "first": {"applicant_id": "32345678901234567", "status": "pending"},
            "second": {"user_id": "32345678901234567", "status": "interview"},
            "bad": {"user_id": "12345", "discord_id": "12345"},
        }
        self.assertEqual(
            [row["application_id"] for row in duplicates.find_duplicate_applications(apps, "first")],
            ["second"],
        )
        self.assertEqual(duplicates.find_duplicate_applications(apps, "bad"), [])

    def test_current_application_is_excluded_and_unusable_inputs_return_empty(self):
        self.assertEqual(duplicates.find_duplicate_applications(self.apps, "missing"), [])
        self.assertEqual(duplicates.find_duplicate_applications([], "APP-001"), [])
        self.assertEqual(duplicates.find_duplicate_applications(self.apps, "APP-004"), [])
        self.assertEqual(
            duplicates.find_duplicate_applications({"one": {"user_id": "12345678901234567"}}, "one"),
            [],
        )

    def test_results_are_bounded_and_contain_no_private_application_content(self):
        apps = {
            f"APP-{index:03d}": {
                "user_id": "42345678901234567",
                "status": "pending",
                "created_at": f"2026-11-{index:02d}",
                "name": f"Name {index}",
                "text": f"Secret motivation {index}",
            }
            for index in range(1, 15)
        }
        matches = duplicates.find_duplicate_applications(apps, "APP-001", limit=5)
        self.assertEqual(len(matches), 5)
        self.assertNotIn("name", matches[0])
        self.assertNotIn("text", matches[0])
        self.assertNotIn("user_id", matches[0])

    def test_rendered_hint_escapes_html_and_never_displays_private_fields(self):
        apps = copy.deepcopy(self.apps)
        apps["<script>alert(1)</script>"] = {
            "user_id": "12345678901234567",
            "status": "<img src=x onerror=alert(1)>",
            "created_at": "2026-11-05",
            "name": "Should not be shown",
            "text": "Private candidate text",
        }
        rendered = duplicates.render_duplicate_notice(apps, "APP-001")
        self.assertIn("Mögliche weitere Bewerbung", rendered)
        self.assertIn("Prüfhinweis", rendered)
        self.assertIn("APP-002", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("<img", rendered)
        self.assertNotIn("Private Motivation", rendered)
        self.assertNotIn("Private candidate text", rendered)
        self.assertNotIn("Interne Recruiter-Notiz", rendered)
        self.assertNotIn("Anderer Anzeigename", rendered)
        self.assertEqual(apps["APP-001"]["status"], "pending")

    def test_no_hint_is_rendered_when_no_verified_duplicate_exists(self):
        self.assertEqual(
            duplicates.render_duplicate_notice(self.apps, "APP-003"),
            "",
        )


class DuplicateHintIntegrationContractTests(unittest.TestCase):
    def test_main_application_page_only_renders_hints_for_reviewers(self):
        source = function_source("webserver.py", "applications_page")
        self.assertIn("can_review = can_review_applications(ctx)", source)
        self.assertIn("duplicate_detection.render_duplicate_notice(apps, app_id, esc)", source)
        self.assertIn("if can_review else", source)
        self.assertIn("{duplicate_notice}", source)

    def test_suite_application_pages_require_recruiting_permission_before_matching(self):
        source = (ROOT / "cogs/pulse_suite.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {
            node.name: ast.get_source_segment(source, node) or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
        }
        list_page = functions["applications_page"]
        detail_page = functions["application_page"]
        for page in (list_page, detail_page):
            self.assertIn("webserver.can_review_applications(ctx)", page)
            self.assertIn("pulse_application_duplicates", page)
            self.assertLess(
                page.index("webserver.can_review_applications(ctx)"),
                page.index("render_duplicate_notice"),
            )

    def test_matcher_does_not_change_application_files_or_decisions(self):
        source = (ROOT / "pulse_application_duplicates.py").read_text(encoding="utf-8")
        self.assertNotIn("save_json", source)
        self.assertNotIn("delete_application", source)
        self.assertNotIn('["status"] =', source)
        self.assertNotIn("update_entry", source)


if __name__ == "__main__":
    unittest.main()
