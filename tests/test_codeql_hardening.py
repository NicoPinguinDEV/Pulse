"""CodeQL findings: request values never leave their directory, the site, or a <script> unchecked."""
import os
import tempfile
import time
import unittest

from fastapi import HTTPException

import pulse_admin
import pulse_social_utils
import pulse_ultimate
import webserver as ws
from pulse_safe_path import contained_file

CAREER = next(iter(ws.APPLICATION_CAREERS))


class ContainedFileTests(unittest.TestCase):
    def test_plain_name_stays_inside(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(contained_file(tmp, "a.json"), os.path.join(os.path.realpath(tmp), "a.json"))

    def test_traversal_is_reduced_to_the_file_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(contained_file(tmp, "../../.env"), os.path.join(os.path.realpath(tmp), ".env"))

    def test_nothing_or_dots_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("", None, ".", ".."):
                self.assertIsNone(contained_file(tmp, name), name)

    def test_backup_path_needs_an_existing_backup_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = ws.BACKUP_DIR
            ws.BACKUP_DIR = tmp
            try:
                name = "backup_20260101_120000.json"
                if not ws.BACKUP_NAME_RE.fullmatch(name):
                    self.skipTest("backup naming scheme changed")
                self.assertIsNone(ws.safe_backup_path(name))
                with open(os.path.join(tmp, name), "w", encoding="utf-8") as fh:
                    fh.write("{}")
                self.assertEqual(ws.safe_backup_path("../" + name), os.path.join(os.path.realpath(tmp), name))
            finally:
                ws.BACKUP_DIR = old


class LocalRedirectTests(unittest.TestCase):
    def test_site_paths_pass(self):
        for url in ("/", "/admin/security#rule-a", "/apply?career=x"):
            self.assertEqual(ws.local_path(url), url)

    def test_foreign_targets_fall_back(self):
        for url in ("https://evil.example", "//evil.example", "/\\evil.example", "\\\\evil.example",
                    "javascript:alert(1)", "admin", ""):
            self.assertEqual(ws.local_path(url), "/", url)

    def test_back_keeps_message_and_anchor(self):
        response = ws.back("/admin/security#rules", "Gespeichert", True)
        self.assertEqual(response.headers["location"], "/admin/security?msg=Gespeichert&t=ok#rules")
        self.assertTrue(ws.back("//evil.example", "x").headers["location"].startswith("/?msg="))


class ApplyCareerTests(unittest.TestCase):
    def test_known_career_returns_configured_key_only(self):
        self.assertEqual(ws.known_career(CAREER), CAREER)
        self.assertEqual(ws.known_career("x</script>"), "")
        self.assertEqual(ws.known_career(None), "")

    def test_draft_key_contains_only_digits_and_a_known_career(self):
        self.assertEqual(ws.apply_draft_key("4242", CAREER), f"pulse:apply-draft:v1:4242:{CAREER}")
        self.assertEqual(ws.apply_draft_key(4242, CAREER), f"pulse:apply-draft:v1:4242:{CAREER}")
        self.assertEqual(ws.apply_draft_key("</script>", "<b>"), "pulse:apply-draft:v1:0:")


class MemberIdTests(unittest.TestCase):
    def test_digits_only(self):
        self.assertEqual(pulse_ultimate.member_path_id(" 123 "), 123)
        for bad in ("", "12a", "//evil", "1" * 21):
            with self.assertRaises(HTTPException):
                pulse_ultimate.member_path_id(bad)


class HostTests(unittest.TestCase):
    def test_host_is_requires_the_domain_or_a_subdomain(self):
        self.assertTrue(pulse_social_utils.host_is("www.youtube.com", "youtube.com"))
        self.assertTrue(pulse_social_utils.host_is("YouTube.com.", "youtube.com"))
        self.assertFalse(pulse_social_utils.host_is("evilyoutube.com", "youtube.com"))
        self.assertFalse(pulse_social_utils.host_is("youtube.com.evil.example", "youtube.com"))
        self.assertFalse(pulse_social_utils.host_is(None, "youtube.com"))

    def test_video_id_ignores_lookalike_hosts(self):
        self.assertIsNone(pulse_social_utils.youtube_video_id("https://evilyoutube.com/watch?v=dQw4w9WgXcQ"))
        self.assertEqual(pulse_social_utils.youtube_video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")


class PreviewMarkdownTests(unittest.TestCase):
    def test_italic_and_bold(self):
        self.assertEqual(pulse_admin._inline_markdown("*a* **b**"), "<em>a</em> <strong>b</strong>")

    def test_many_stars_stay_fast(self):
        started = time.perf_counter()
        pulse_admin._inline_markdown("*" + "*a" * 20000)
        self.assertLess(time.perf_counter() - started, 1.0)


if __name__ == "__main__":
    unittest.main()
