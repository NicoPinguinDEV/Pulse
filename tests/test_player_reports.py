"""Guild-isolated player report persistence, evidence validation and review tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_player_reports as reports


class PlayerReportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(reports, "DB_PATH", str(Path(self.tmp.name) / "pulse_suite.db"))
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def create(self, guild_id="100", reporter_id="42", **overrides):
        values = {
            "guild_id": guild_id,
            "reporter_id": reporter_id,
            "reporter_name": "Community Member",
            "target_name": "RP-Spieler 17",
            "category": "rule_violation",
            "description": "Der gemeldete Spieler hat wiederholt die geltenden Regeln missachtet.",
            "priority": "normal",
        }
        values.update(overrides)
        return reports.create_report(**values)

    def test_report_is_guild_scoped_and_reporter_can_see_own_status(self):
        first = self.create()
        other = self.create("200", "77", target_name="Anderer Spieler")
        self.assertEqual([x["id"] for x in reports.list_reports("100")], [first["id"]])
        self.assertEqual([x["id"] for x in reports.list_user_reports("100", "42")], [first["id"]])
        self.assertEqual(reports.list_user_reports("100", "77"), [])
        self.assertEqual([x["id"] for x in reports.list_reports("200")], [other["id"]])

    def test_manager_review_is_bound_to_report_guild_and_records_reviewer(self):
        row = self.create()
        updated = reports.update_report("100", row["id"], "in_progress", "88", "Supervisor", "99", "Leitung")
        self.assertEqual(updated["status"], "in_progress")
        self.assertEqual(updated["assignee_id"], "88")
        self.assertEqual(updated["reviewed_by_id"], "99")
        with self.assertRaises(KeyError):
            reports.update_report("200", row["id"], "closed", "", "", "99", "Leitung")
        with self.assertRaises(ValueError):
            reports.update_report("100", row["id"], "unknown", "", "", "99", "Leitung")

    def test_only_whitelisted_discord_evidence_is_retained(self):
        evidence = {
            "filename": "screen-shot.png",
            "content_type": "image/png",
            "size": 1024,
            "url": "https://cdn.discordapp.com/attachments/123/456/screen-shot.png",
        }
        row = self.create(evidence=evidence)
        self.assertEqual(json.loads(row["evidence_json"])[0]["filename"], "screen-shot.png")
        self.assertEqual(reports.list_reports("100")[0]["evidence"][0]["content_type"], "image/png")
        invalid = dict(evidence, url="https://example.com/not-discord.png")
        with self.assertRaises(ValueError):
            self.create(evidence=invalid)

    def test_evidence_size_type_and_description_are_limited(self):
        base = {
            "filename": "large.png",
            "content_type": "image/png",
            "size": reports.MAX_EVIDENCE_BYTES + 1,
            "url": "https://cdn.discordapp.com/attachments/123/456/large.png",
        }
        with self.assertRaises(ValueError):
            self.create(evidence=base)
        with self.assertRaises(ValueError):
            self.create(evidence=dict(base, size=50, filename="payload.exe"))
        with self.assertRaises(ValueError):
            self.create(description="Zu kurz")
        with self.assertRaises(ValueError):
            self.create(description="x" * 3001)

    def test_invalid_category_priority_target_and_ids_are_rejected(self):
        for kwargs in (
            {"guild_id": "not-a-guild"},
            {"reporter_id": "not-a-user"},
            {"category": "unknown"},
            {"priority": "critical"},
            {"target_name": "x"},
        ):
            with self.assertRaises(ValueError):
                self.create(**kwargs)

    def test_result_limits_are_bounded(self):
        self.create(target_name="RP-Spieler A")
        self.create(target_name="RP-Spieler B")
        self.assertEqual(len(reports.list_reports("100", limit=1)), 1)
        self.assertEqual(len(reports.list_user_reports("100", "42", limit=1)), 1)


if __name__ == "__main__":
    unittest.main()
