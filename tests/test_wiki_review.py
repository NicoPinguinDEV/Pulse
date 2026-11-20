"""Audit #111: redaktionelle Freigabe für Wiki-Versionen (Vier-Augen-Prinzip, Zustand je Version, Routen, Rechte)."""
import unittest

import pulse_db
import pulse_wiki_review as review
from tests.test_wiki_plus import MANAGER, TEAM, Base


class ReviewRuleTests(Base):
    def test_state_starts_empty_and_pending_after_request(self):
        self.assertEqual(review.state(self.page)["state"], "none")
        self.assertEqual(review.request_review(self.page, "1", "Ann"), (True, "ok"))
        self.assertEqual(review.state(self.page)["state"], "pending")
        self.assertEqual(review.request_review(self.page, "1", "Ann"), (False, "already_pending"))
        self.assertEqual([p["id"] for p in review.pending_pages()], [self.page])

    def test_requester_and_author_cannot_decide_their_own(self):
        review.request_review(self.page, "1", "Ann")
        self.assertEqual(review.decide(self.page, "1", "Ann", True), (False, "own_request"))
        self.assertEqual(review.decide(self.page, "9", "Lead", True), (False, "own_version"))  # Autor der Version
        self.assertEqual(review.state(self.page)["state"], "pending")
        self.assertEqual(review.decide(self.page, "2", "Bo", True), (True, "ok"))
        self.assertEqual(review.state(self.page)["state"], "approved")
        self.assertEqual(review.request_review(self.page, "3", "Cy"), (False, "already_approved"))

    def test_concurrent_decision_cannot_overwrite_the_first_result(self):
        review.request_review(self.page, "1", "Ann")
        original_row = review._row

        def stale_pending_row(page_id, version):
            row = original_row(page_id, version)
            # Simulate a second manager committing after this request read the
            # pending row but before its conditional UPDATE.
            with pulse_db.connect() as db:
                db.execute(
                    "UPDATE wiki_version_reviews SET status='approved',reviewer_id='3',"
                    "reviewer_name='Cy',decided_at='2026-11-20T12:00:00+00:00' "
                    "WHERE page_id=? AND version=? AND status='pending'",
                    (str(page_id), version),
                )
            return row

        from unittest.mock import patch
        with patch.object(review, "_row", side_effect=stale_pending_row):
            self.assertEqual(
                review.decide(self.page, "2", "Bo", False, "Bitte Quelle ergänzen"),
                (False, "not_pending"),
            )
        state = review.state(self.page)
        self.assertEqual(state["state"], "approved")
        saved = review._row(self.page, state["version"])
        self.assertEqual(saved["status"], "approved")
        self.assertEqual(saved["reviewer_id"], "3")

    def test_rejection_needs_a_comment_and_allows_a_new_request(self):
        review.request_review(self.page, "1", "Ann")
        self.assertEqual(review.decide(self.page, "2", "Bo", False, "  "), (False, "comment_required"))
        self.assertEqual(review.decide(self.page, "2", "Bo", False, "Bitte Quelle ergänzen"), (True, "ok"))
        st = review.state(self.page)
        self.assertEqual((st["state"], st["comment"]), ("rejected", "Bitte Quelle ergänzen"))
        self.assertEqual(review.request_review(self.page, "1", "Ann"), (True, "ok"))

    def test_a_new_version_needs_a_new_approval(self):
        review.request_review(self.page, "1", "Ann")
        review.decide(self.page, "2", "Bo", True)
        self.save("Dienstanweisung Funk", "Regeln", "Neuer Text, geändert.", page_id=self.page)
        self.assertEqual(review.state(self.page)["state"], "none")

    def test_unknown_page_and_nothing_pending(self):
        self.assertEqual(review.request_review("nope", "1"), (False, "not_found"))
        self.assertEqual(review.decide(self.page, "2", "Bo", True), (False, "not_pending"))


class ReviewRouteTests(Base):
    def post(self, page, **data):
        return self.client.post(f"/wissen/{page}/freigabe", data=data, follow_redirects=False)

    def test_team_member_is_refused_without_any_write(self):
        with self.as_user(TEAM):
            self.assertEqual(self.post(self.page, aktion="einreichen").status_code, 403)
        self.assertEqual(review.state(self.page)["state"], "none")

    def test_manager_flow_and_four_eyes_over_http(self):
        with self.as_user(MANAGER, "1"):
            self.assertEqual(self.post(self.page, aktion="einreichen").status_code, 303)
            self.assertIn("nicht+selbst", self.post(self.page, aktion="freigeben").headers["location"].replace("%20", "+"))
        self.assertEqual(review.state(self.page)["state"], "pending")
        with self.as_user(MANAGER, "2"):
            self.assertEqual(self.post(self.page, aktion="freigeben").status_code, 303)
        self.assertEqual(review.state(self.page)["state"], "approved")

    def test_unknown_page_action_and_escaping(self):
        with self.as_user(MANAGER, "1"):
            self.assertEqual(self.post("nope", aktion="einreichen").status_code, 404)
            self.assertEqual(self.post(self.page, aktion="loeschen").status_code, 400)
            review.request_review(self.page, "1", "<b>Ann</b>")
            html = self.client.get(f"/wiki/{self.page}").text
        self.assertIn("Redaktionelle Freigabe", html)
        self.assertNotIn("<b>Ann</b>", html)

    def test_backup_table_list_contains_the_review_table(self):
        self.assertIn("wiki_version_reviews", pulse_db.TABLES)


if __name__ == "__main__":
    unittest.main()
