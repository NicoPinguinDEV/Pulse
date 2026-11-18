"""Regression tests for approval decisions in the Pulse Next personnel workflow."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

import pulse_db
import pulse_next
import pulse_ultimate
import webserver
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401


class NextApprovalDecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.temp.cleanup)
        self.db_path = str(Path(self.temp.name) / "pulse-next-approvals.db")
        self.db_patch = patch.object(pulse_db, "DB_PATH", self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)

        self.previous_next_init = pulse_next._INIT
        self.previous_ultimate_init = pulse_ultimate._INIT
        pulse_next._INIT = False
        pulse_ultimate._INIT = False
        self.addCleanup(setattr, pulse_next, "_INIT", self.previous_next_init)
        self.addCleanup(setattr, pulse_ultimate, "_INIT", self.previous_ultimate_init)
        pulse_next._APPROVAL_DECISIONS_IN_PROGRESS.clear()
        self.addCleanup(pulse_next._APPROVAL_DECISIONS_IN_PROGRESS.clear)
        pulse_next.setup_next()

        self.actor = SimpleNamespace(id=1, top_role=None)
        self.target = SimpleNamespace(id=2)
        self.guild = SimpleNamespace(
            get_member=lambda user_id: self.target if int(user_id) == 2 else None
        )
        self.ctx = SimpleNamespace(
            guild=self.guild,
            member=self.actor,
            perms={"is_admin": False, "can_approve": True},
            user={"id": "1", "global_name": "Leitung"},
        )
        self._insert_pending_promotion()

    def _insert_pending_promotion(self, requester_id="9"):
        with pulse_next.cx() as connection:
            connection.execute(
                "INSERT INTO next_promotions("
                "id,user_id,user_name,from_rank,to_rank,reason,target_role_id,old_role_id,status,"
                "requested_by_id,requested_by_name,created_at,updated_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "promotion-1", "2", "Zielmitglied", "Supporter", "Moderator", "Gute Arbeit",
                    "20", "10", "pending", requester_id, "Antragsteller", "2026-11-18T05:00:00+00:00",
                    "2026-11-18T05:00:00+00:00",
                ),
            )
            connection.execute(
                "INSERT INTO next_approvals("
                "id,object_type,object_id,action,requester_id,requester_name,status,"
                "approver_id,approver_name,note,created_at,decided_at"
                ") VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "approval-1", "promotion", "promotion-1", "promotion", requester_id,
                    "Antragsteller", "pending", None, None, "", "2026-11-18T05:00:00+00:00", None,
                ),
            )

    def _statuses(self):
        with pulse_next.cx() as connection:
            approval = connection.execute(
                "SELECT status FROM next_approvals WHERE id='approval-1'"
            ).fetchone()
            promotion = connection.execute(
                "SELECT status FROM next_promotions WHERE id='promotion-1'"
            ).fetchone()
        return approval["status"], promotion["status"]

    def _run(self, decision, promotion_result="Beförderung angewendet.", lockout=(True, "")):
        with patch.object(
            webserver,
            "enforce_team_safety_action_lockout",
            new=AsyncMock(return_value=lockout),
        ) as lockout_mock, patch.object(
            pulse_next,
            "promote_now",
            new=AsyncMock(return_value=promotion_result),
        ) as promote_mock, patch.object(pulse_next, "save_audit") as audit_mock, patch.object(
            pulse_next, "event"
        ) as event_mock:
            result = asyncio.run(
                pulse_next.decide_approval(self.ctx, "approval-1", decision, "Entscheidungsnotiz")
            )
        return result, lockout_mock, promote_mock, audit_mock, event_mock

    def test_failed_discord_role_grant_keeps_approval_and_promotion_pending(self):
        with patch.object(
            webserver,
            "enforce_team_safety_action_lockout",
            new=AsyncMock(return_value=(True, "")),
        ), patch.object(
            pulse_next,
            "promote_now",
            new=AsyncMock(return_value="Discord hat die Rollenvergabe abgelehnt."),
        ), patch.object(pulse_next, "save_audit") as audit_mock:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(pulse_next.decide_approval(self.ctx, "approval-1", "approve", "Bitte prüfen"))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("Discord hat die Rollenvergabe abgelehnt", str(raised.exception.detail))
        self.assertEqual(self._statuses(), ("pending", "pending"))
        audit_mock.assert_called_once()
        self.assertEqual(audit_mock.call_args.args[1], "Freigabe fehlgeschlagen")

    def test_successful_discord_role_grant_completes_both_records(self):
        result, lockout_mock, promote_mock, audit_mock, _ = self._run("approve")

        self.assertEqual(result, "Beförderung angewendet.")
        self.assertEqual(self._statuses(), ("approved", "approved"))
        lockout_mock.assert_awaited_once()
        promote_mock.assert_awaited_once()
        audit_mock.assert_called_once()

    def test_rejection_marks_records_rejected_without_calling_discord(self):
        result, lockout_mock, promote_mock, _, event_mock = self._run("reject")

        self.assertEqual(result, "Freigabe gespeichert.")
        self.assertEqual(self._statuses(), ("rejected", "rejected"))
        lockout_mock.assert_not_awaited()
        promote_mock.assert_not_awaited()
        event_mock.assert_called_once()
        self.assertEqual(event_mock.call_args.args[1], "promotion_rejected")

    def test_lockout_refusal_does_not_consume_approval(self):
        with patch.object(
            webserver,
            "enforce_team_safety_action_lockout",
            new=AsyncMock(return_value=(False, "Teamler-Schutz aktiv")),
        ), patch.object(pulse_next, "promote_now", new=AsyncMock()) as promote_mock:
            with self.assertRaises(HTTPException) as raised:
                asyncio.run(pulse_next.decide_approval(self.ctx, "approval-1", "approve"))

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(self._statuses(), ("pending", "pending"))
        promote_mock.assert_not_awaited()

    def test_invalid_decision_is_rejected_instead_of_becoming_a_rejection(self):
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(pulse_next.decide_approval(self.ctx, "approval-1", "maybe"))

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(self._statuses(), ("pending", "pending"))

    def test_requester_cannot_approve_own_request(self):
        self._insert_pending_promotion(requester_id="1")
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(pulse_next.decide_approval(self.ctx, "approval-1", "approve"))

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(self._statuses(), ("pending", "pending"))

    def test_duplicate_in_progress_decision_is_rejected(self):
        pulse_next._APPROVAL_DECISIONS_IN_PROGRESS.add("approval-1")
        with self.assertRaises(HTTPException) as raised:
            asyncio.run(pulse_next.decide_approval(self.ctx, "approval-1", "approve"))

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(self._statuses(), ("pending", "pending"))


if __name__ == "__main__":
    unittest.main()
