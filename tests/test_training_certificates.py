"""Audit #109/#110: certificates after a passed training and refresher reminders."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

import pulse_certificates as certs
import pulse_db
import pulse_features
import webserver as ws

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)


class CertificateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.training = pulse_db.create_training(
            "Support Grundlagen", "", 80, 20, "900", "Leitung",
            [{"question": "A?", "options": ["A", "B"], "answer_index": 0}],
        )
        self.sent = []

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def notifier(self, *args):
        self.sent.append(args)

    def attempt(self, passed=True, user="123"):
        return pulse_db.save_attempt(self.training, user, "Person", 90 if passed else 40, passed)

    def test_failed_attempt_gets_no_certificate_and_passed_one_is_idempotent(self):
        self.assertIsNone(certs.issue_for_attempt(self.attempt(False), NOW))
        attempt_id = self.attempt()
        first = certs.issue_for_attempt(attempt_id, NOW)
        again = certs.issue_for_attempt(attempt_id, NOW + timedelta(days=1))
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(len(certs.certificates_for_user("123", NOW)), 1)
        self.assertIsNone(first["valid_until"])
        self.assertEqual(certs.status(first, NOW + timedelta(days=9999)), "valid")

    def test_rule_validation(self):
        with self.assertRaises(ValueError):
            certs.set_rule("missing", 30, 7, "900")
        with self.assertRaises(ValueError):
            certs.set_rule(self.training, 30, 30, "900")
        with self.assertRaises(ValueError):
            certs.set_rule(self.training, 99999, 7, "900")
        certs.set_rule(self.training, 30, 7, "900")
        self.assertEqual(certs.get_rule(self.training)["valid_days"], 30)
        certs.set_rule(self.training, 0, 0, "900")
        self.assertIsNone(certs.get_rule(self.training))

    def test_validity_follows_rule_and_new_certificate_supersedes_old(self):
        certs.set_rule(self.training, 30, 7, "900")
        old = certs.issue_for_attempt(self.attempt(), NOW)
        self.assertEqual(old["valid_until"], (NOW + timedelta(days=30)).isoformat(timespec="seconds"))
        self.assertEqual(certs.status(old, NOW + timedelta(days=10)), "valid")
        self.assertEqual(certs.status(old, NOW + timedelta(days=24)), "expiring")
        self.assertEqual(certs.status(old, NOW + timedelta(days=30)), "expired")
        new = certs.issue_for_attempt(self.attempt(), NOW + timedelta(days=35))
        by_id = {c["id"]: c for c in certs.certificates_for_user("123", NOW + timedelta(days=35))}
        self.assertEqual(by_id[old["id"]]["status"], "superseded")
        self.assertEqual(by_id[new["id"]]["status"], "valid")

    def test_notices_are_sent_once_per_kind_to_the_holder_only(self):
        certs.set_rule(self.training, 30, 7, "900")
        certs.issue_for_attempt(self.attempt(user="123"), NOW)
        certs.issue_for_attempt(self.attempt(user="456"), NOW + timedelta(days=20))
        self.assertEqual(certs.process_notices(NOW + timedelta(days=10), self.notifier), {"warn": 0, "expired": 0})
        self.assertEqual(certs.process_notices(NOW + timedelta(days=24), self.notifier), {"warn": 1, "expired": 0})
        self.assertEqual(certs.process_notices(NOW + timedelta(days=25), self.notifier), {"warn": 0, "expired": 0})
        self.assertEqual(certs.process_notices(NOW + timedelta(days=31), self.notifier), {"warn": 0, "expired": 1})
        self.assertEqual(certs.process_notices(NOW + timedelta(days=32), self.notifier), {"warn": 0, "expired": 0})
        self.assertEqual([m[0] for m in self.sent], ["123", "123"])

    def test_superseded_certificate_triggers_no_notice(self):
        certs.set_rule(self.training, 30, 7, "900")
        certs.issue_for_attempt(self.attempt(), NOW)
        certs.issue_for_attempt(self.attempt(), NOW + timedelta(days=20))
        self.assertEqual(certs.process_notices(NOW + timedelta(days=31), self.notifier), {"warn": 0, "expired": 0})

    def test_verify_hides_score_and_attention_list_is_for_current_certificates(self):
        certs.set_rule(self.training, 30, 7, "900")
        cert = certs.issue_for_attempt(self.attempt(), NOW)
        found = certs.verify(cert["verify_code"].lower(), NOW)
        self.assertEqual(found["id"], cert["id"])
        self.assertNotIn("score", found)
        self.assertIsNone(certs.verify("nope", NOW))
        self.assertIsNone(certs.verify("x" * 99, NOW))
        self.assertEqual(len(certs.attention_list(NOW + timedelta(days=40))), 1)
        self.assertEqual(certs.attention_list(NOW), [])

    def test_tables_are_part_of_backup_state_roundtrip(self):
        certs.set_rule(self.training, 30, 7, "900")
        cert = certs.issue_for_attempt(self.attempt(), NOW)
        state = pulse_db.export_state()
        for table in ("training_certificates", "training_refresher_rules", "training_certificate_notices"):
            self.assertIn(table, state)
        pulse_db.import_state(state)
        self.assertEqual(certs.verify(cert["verify_code"], NOW)["id"], cert["id"])


class CertificateRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.qs = [{"question": "A?", "options": ["A", "B"], "answer_index": 0}]
        self.training = pulse_db.create_training("Grundlagen", "", 80, 20, "900", "Leitung", self.qs)
        guild = SimpleNamespace(members=[], roles=[], get_member=lambda _id: None)
        base = dict(guild=guild, config={"team_role_ids": []}, msg="", msg_ok=True, guild_name="Test")
        self.member = SimpleNamespace(user={"id": "123", "global_name": "Mitglied"}, perms={"can_view_dashboard": True}, **base)
        self.other = SimpleNamespace(user={"id": "456", "global_name": "Andere"}, perms={"can_view_dashboard": True}, **base)
        self.manager = SimpleNamespace(user={"id": "900", "global_name": "Leitung"},
                                       perms={"can_view_dashboard": True, "can_manage_training": True}, **base)
        app = FastAPI()
        pulse_features.register(app)
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def as_user(self, who):
        return patch.object(ws, "auth", return_value=who)

    def test_passing_submit_issues_certificate_and_failing_does_not(self):
        question_id = pulse_db.get_training(self.training)["questions"][0]["id"]
        with self.as_user(self.member), patch.object(ws, "log_audit"):
            failed = self.client.post(f"/training/{self.training}/submit", data={f"q_{question_id}": "1"})
            self.assertEqual(certs.certificates_for_user("123"), [])
            passed = self.client.post(f"/training/{self.training}/submit", data={f"q_{question_id}": "0"})
        self.assertEqual((failed.status_code, passed.status_code), (200, 200))
        issued = certs.certificates_for_user("123")
        self.assertEqual(len(issued), 1)
        self.assertIn(issued[0]["verify_code"], passed.text)

    def test_refresher_rule_requires_training_manager_and_validates(self):
        with self.as_user(self.member):
            self.assertEqual(self.client.post(f"/training/{self.training}/refresher",
                                              data={"valid_days": "30", "warn_days": "7"}).status_code, 403)
        self.assertIsNone(certs.get_rule(self.training))
        with self.as_user(self.manager), patch.object(ws, "log_audit"):
            bad = self.client.post(f"/training/{self.training}/refresher", data={"valid_days": "30", "warn_days": "30"})
            ok = self.client.post(f"/training/{self.training}/refresher", data={"valid_days": "30", "warn_days": "7"},
                                  follow_redirects=False)
        self.assertEqual(bad.status_code, 400)
        self.assertLess(ok.status_code, 400)
        self.assertEqual(certs.get_rule(self.training)["warn_days"], 7)

    def test_certificate_page_is_limited_to_holder_and_managers(self):
        attempt_id = pulse_db.save_attempt(self.training, "123", "Mitglied", 95, True)
        code = certs.issue_for_attempt(attempt_id)["verify_code"]
        with self.as_user(self.member):
            own = self.client.get(f"/training/certificate/{code}")
        with self.as_user(self.other):
            foreign = self.client.get(f"/training/certificate/{code}")
            missing = self.client.get("/training/certificate/NOPE")
        with self.as_user(self.manager):
            managed = self.client.get(f"/training/certificate/{code}")
        self.assertEqual((own.status_code, foreign.status_code, missing.status_code, managed.status_code), (200, 403, 404, 200))
        self.assertNotIn("95%", own.text)


if __name__ == "__main__":
    unittest.main()
