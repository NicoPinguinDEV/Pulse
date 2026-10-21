"""F-02: read-only effective channel visibility report for the unverified role."""
import unittest
from types import SimpleNamespace

from pulse_visibility_audit import audit_unverified_visibility


class FakeChannel:
    def __init__(self, channel_id, name, visible=None, position=0, error=None):
        self.id = channel_id
        self.name = name
        self.position = position
        self.visible = visible
        self.error = error

    def permissions_for(self, role):
        if self.error:
            raise self.error
        return SimpleNamespace(view_channel=self.visible)


class VisibilityAuditTests(unittest.TestCase):
    def setUp(self):
        self.role = SimpleNamespace(id=123)

    def test_visible_non_onboarding_channel_is_review_hint_not_automatic_change(self):
        channel = FakeChannel(10, "regeln", True, 1)
        report = audit_unverified_visibility([channel], self.role, expected_visible_channel_ids=[20])
        self.assertTrue(report["available"])
        self.assertEqual(report["visible_count"], 1)
        self.assertEqual(report["review_count"], 1)
        row = report["rows"][0]
        self.assertTrue(row["viewable"])
        self.assertTrue(row["needs_review"])
        self.assertFalse(row["expected_visible"])
        self.assertTrue(channel.permissions_for(self.role).view_channel)

    def test_expected_verification_channel_is_visible_without_review_warning(self):
        report = audit_unverified_visibility(
            [FakeChannel(20, "verifikation", True)],
            self.role,
            expected_visible_channel_ids=["20", "not-an-id", 0],
        )
        self.assertEqual(report["review_count"], 0)
        self.assertTrue(report["rows"][0]["expected_visible"])

    def test_hidden_channel_is_not_flagged_and_unknown_is_counted(self):
        report = audit_unverified_visibility([
            FakeChannel(10, "hidden", False),
            FakeChannel(11, "unknown", error=RuntimeError("test only")),
        ], self.role)
        self.assertEqual(report["hidden_count"], 1)
        self.assertEqual(report["unknown_count"], 1)
        self.assertEqual(report["review_count"], 0)
        self.assertEqual(report["rows"][1]["error"], "RuntimeError")

    def test_missing_role_returns_clear_unavailable_report(self):
        report = audit_unverified_visibility([FakeChannel(10, "hidden", False)], None)
        self.assertFalse(report["available"])
        self.assertEqual(report["rows"], [])
        self.assertIn("Unverifiziert-Rolle", report["reason"])

    def test_channels_are_sorted_by_position_then_name(self):
        report = audit_unverified_visibility([
            FakeChannel(2, "zwei", False, position=2),
            FakeChannel(3, "alpha", False, position=1),
            FakeChannel(1, "beta", False, position=1),
        ], self.role)
        self.assertEqual([row["channel_id"] for row in report["rows"]], [3, 1, 2])


if __name__ == "__main__":
    unittest.main()
