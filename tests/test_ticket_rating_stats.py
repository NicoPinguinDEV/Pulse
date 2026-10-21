"""BOT-06: rating analytics are grouped by ticket category and assigned staff."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pulse_db


class TicketRatingStatsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch_db = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.patch_db.start()
        pulse_db.init_db()
        self._make_rated_ticket("g1", "support", 10, "Team A", 5)
        self._make_rated_ticket("g1", "support", 10, "Team A", 3)
        self._make_rated_ticket("g1", "bug", 20, "Team B", 4)
        self._make_rated_ticket("g2", "support", 99, "Other guild", 1)

    def tearDown(self):
        self.patch_db.stop()
        self.tmp.cleanup()

    def _make_rated_ticket(self, guild_id, category, staff_id, staff_name, rating):
        ticket_id = pulse_db.create_ticket(
            channel_id=int(f"{guild_id == 'g2'}{staff_id}{rating}{len(pulse_db.list_tickets(limit=2000)) + 100}"),
            guild_id=guild_id, user_id=staff_id + rating, user_name="Test", category=category,
        )
        self.assertTrue(pulse_db.claim_ticket(ticket_id, staff_id, staff_name))
        self.assertTrue(pulse_db.close_ticket(
            ticket_id, "test", closed_by_id=staff_id, closed_by_name=staff_name,
        ))
        self.assertTrue(pulse_db.rate_ticket(ticket_id, rating))

    def test_category_and_staff_ratings_are_aggregated_and_guild_scoped(self):
        stats = pulse_db.ticket_rating_stats("g1")
        categories = {row["category"]: row for row in stats["categories"]}
        self.assertEqual(categories["support"]["rated_count"], 2)
        self.assertEqual(categories["support"]["average_rating"], 4.0)
        self.assertEqual(categories["bug"]["average_rating"], 4.0)
        staff = {row["user_id"]: row for row in stats["staff"]}
        self.assertEqual(staff["10"]["rated_count"], 2)
        self.assertEqual(staff["10"]["average_rating"], 4.0)
        self.assertNotIn("99", staff)

    def test_unrated_ticket_does_not_change_rating_statistics(self):
        pulse_db.create_ticket(
            channel_id=998877, guild_id="g1", user_id=77, user_name="No rating",
            category="support",
        )
        stats = pulse_db.ticket_rating_stats("g1")
        support = next(row for row in stats["categories"] if row["category"] == "support")
        self.assertEqual(support["rated_count"], 2)
        self.assertEqual(support["average_rating"], 4.0)

    def test_limit_is_bounded(self):
        self.assertEqual(len(pulse_db.ticket_rating_stats("g1", limit=0)["categories"]), 2)
        self.assertLessEqual(len(pulse_db.ticket_rating_stats("g1", limit=1000)["categories"]), 20)


if __name__ == "__main__":
    unittest.main()
