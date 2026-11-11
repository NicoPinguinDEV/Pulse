"""Tests for guild-isolated Discord community suggestions and review."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db
import pulse_community_feedback as feedback


class CommunityFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def submit(self, guild_id="100", **overrides):
        values = {
            "guild_id": guild_id,
            "author_id": "42",
            "author_name": "Community Member",
            "category": "gameplay",
            "title": "Ein besseres Vorschlagsmenü",
            "content": "Ein klarer Vorschlagsbereich würde die Rückmeldungen besser bündeln.",
            "anonymous": False,
        }
        values.update(overrides)
        return feedback.submit_feedback(**values)

    def test_suggestion_is_stored_and_returned_for_its_guild(self):
        row = self.submit()
        self.assertEqual(row["status"], "new")
        self.assertEqual(row["guild_id"], "100")
        self.assertEqual(row["author_id"], "42")
        self.assertEqual(row["author_name"], "Community Member")
        self.assertEqual([x["id"] for x in feedback.list_feedback("100")], [row["id"]])
        self.assertEqual(feedback.list_feedback("200"), [])

    def test_anonymous_submission_does_not_retain_author_identity(self):
        row = self.submit(anonymous=True)
        saved = feedback.list_feedback("100")[0]
        self.assertEqual(row["anonymous"], 1)
        self.assertIsNone(saved["author_id"])
        self.assertEqual(saved["author_name"], "Anonym")

    def test_suggestions_are_strictly_guild_scoped(self):
        first = self.submit("100")
        second = self.submit("200", author_id="77", title="Anderer Server-Vorschlag")
        self.assertEqual([x["id"] for x in feedback.list_feedback("100")], [first["id"]])
        self.assertEqual([x["id"] for x in feedback.list_feedback("200")], [second["id"]])
        with self.assertRaises(KeyError):
            feedback.update_feedback("200", first["id"], "done", "", 9, "Reviewer")

    def test_review_requires_valid_status_and_records_reviewer(self):
        row = self.submit()
        reviewed = feedback.update_feedback(
            "100", row["id"], "planned", "Wird für das nächste Update bewertet.", 9, "Leitung"
        )
        self.assertEqual(reviewed["status"], "planned")
        self.assertEqual(reviewed["manager_note"], "Wird für das nächste Update bewertet.")
        self.assertEqual(reviewed["reviewed_by_id"], "9")
        self.assertEqual(reviewed["reviewed_by_name"], "Leitung")
        with self.assertRaises(ValueError):
            feedback.update_feedback("100", row["id"], "unknown", "", 9, "Leitung")

    def test_input_validation_rejects_bad_ids_categories_and_lengths(self):
        with self.assertRaises(ValueError):
            self.submit(guild_id="not-a-guild")
        with self.assertRaises(ValueError):
            self.submit(author_id="x")
        with self.assertRaises(ValueError):
            self.submit(category="../admin")
        with self.assertRaises(ValueError):
            self.submit(title="No")
        with self.assertRaises(ValueError):
            self.submit(content="Too short")
        with self.assertRaises(ValueError):
            self.submit(content="x" * 3001)

    def test_general_feedback_is_distinguished_and_cannot_open_a_poll(self):
        row = self.submit(kind="feedback")
        self.assertEqual(row["kind"], "feedback")
        stored = feedback.list_feedback("100")
        self.assertEqual(stored[0]["kind"], "feedback")
        with self.assertRaisesRegex(ValueError, "Nur Community-Vorschläge"):
            feedback.update_feedback("100", row["id"], "voting", "", 9, "Leitung")
        self.assertEqual(feedback.list_voting_feedback("100"), [])

    def test_community_votes_are_unique_per_member_and_can_be_changed(self):
        row = self.submit()
        with self.assertRaises(KeyError):
            feedback.cast_vote("100", row["id"], 42, "for")
        feedback.update_feedback("100", row["id"], "voting", "", 9, "Leitung")

        first = feedback.cast_vote("100", row["id"], 42, "for")
        repeated = feedback.cast_vote("100", row["id"], 42, "for")
        changed = feedback.cast_vote("100", row["id"], 42, "against")
        second_member = feedback.cast_vote("100", row["id"], 43, "for")

        self.assertEqual(first, {"vote": "for", "for": 1, "against": 0})
        self.assertEqual(repeated, {"vote": "for", "for": 1, "against": 0})
        self.assertEqual(changed, {"vote": "against", "for": 0, "against": 1})
        self.assertEqual(second_member, {"vote": "for", "for": 1, "against": 1})
        listing = feedback.list_voting_feedback("100")
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["votes_for"], 1)
        self.assertEqual(listing[0]["votes_against"], 1)

    def test_community_votes_are_isolated_by_guild_and_invalid_choices_rejected(self):
        row = self.submit("100")
        feedback.update_feedback("100", row["id"], "voting", "", 9, "Leitung")
        with self.assertRaises(KeyError):
            feedback.cast_vote("200", row["id"], 42, "for")
        with self.assertRaises(ValueError):
            feedback.cast_vote("100", row["id"], 42, "maybe")
        self.assertEqual(feedback.vote_counts("200", row["id"]), {"for": 0, "against": 0})
        self.assertEqual(feedback.list_voting_feedback("200"), [])


    def test_result_limit_is_bounded_and_none_guild_is_empty(self):
        self.submit(title="Erster sinnvoller Vorschlag")
        self.submit(title="Zweiter sinnvoller Vorschlag")
        self.assertEqual(len(feedback.list_feedback("100", limit=1)), 1)
        self.assertEqual(feedback.list_feedback(None), [])


if __name__ == "__main__":
    unittest.main()
