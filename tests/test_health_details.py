import unittest

from pulse_health import health_details


class HealthDetailsTests(unittest.TestCase):
    def test_gateway_latency_is_reported_in_milliseconds(self):
        details = health_details(type("Bot", (), {"latency": 0.123})(), {})
        self.assertEqual(details["gateway_latency_ms"], 123)

    def test_nonfinite_or_missing_latency_is_unknown(self):
        for latency in (None, float("nan"), float("inf"), -1):
            details = health_details(type("Bot", (), {"latency": latency})(), {})
            self.assertIsNone(details["gateway_latency_ms"])

    def test_last_successful_job_uses_latest_success_timestamp(self):
        details = health_details(None, {"jobs": [
            {"name": "old", "last_ok": "2026-10-20T10:00:00+00:00", "failures": 0},
            {"name": "new", "last_ok": "2026-10-21T10:00:00+00:00", "failures": 1},
            {"name": "never-successful", "last_ok": "", "failures": 4},
        ]})
        self.assertEqual(details["last_successful_job"], "new")
        self.assertEqual(details["last_successful_job_at"], "2026-10-21T10:00:00+00:00")
        self.assertEqual(details["jobs_total"], 3)
        self.assertEqual(details["jobs_failed"], 2)

    def test_event_loop_lag_is_converted_to_milliseconds(self):
        details = health_details(None, {"lag": {"last": 0.25}})
        self.assertEqual(details["event_loop_lag_ms"], 250)

    def test_empty_snapshot_stays_json_safe(self):
        details = health_details(None, None)
        self.assertEqual(details, {
            "gateway_latency_ms": None,
            "event_loop_lag_ms": None,
            "jobs_total": 0,
            "jobs_failed": 0,
            "last_successful_job": None,
            "last_successful_job_at": None,
        })


if __name__ == "__main__":
    unittest.main()
