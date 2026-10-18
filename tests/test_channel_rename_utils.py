import unittest

from pulse_channel_rename_utils import CHANNEL_RENAME_MIN_INTERVAL_SECONDS, rename_wait_seconds


class ChannelRenameThrottleTests(unittest.TestCase):
    def test_interval_stays_inside_discords_two_per_ten_minutes(self):
        self.assertGreaterEqual(CHANNEL_RENAME_MIN_INTERVAL_SECONDS, 300)

    def test_first_rename_runs_immediately(self):
        self.assertEqual(rename_wait_seconds(None, 1000.0), 0.0)

    def test_early_rename_waits_for_remaining_window(self):
        self.assertEqual(rename_wait_seconds(1000.0, 1100.0), 200.0)

    def test_rename_allowed_after_window(self):
        self.assertEqual(rename_wait_seconds(1000.0, 1300.0), 0.0)
        self.assertEqual(rename_wait_seconds(1000.0, 5000.0), 0.0)

    def test_clock_jump_backwards_never_blocks(self):
        self.assertEqual(rename_wait_seconds(5000.0, 1000.0), 0.0)


if __name__ == "__main__":
    unittest.main()
