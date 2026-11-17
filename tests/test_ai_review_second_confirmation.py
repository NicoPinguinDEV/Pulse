"""DISC-5: kick/ban from the AI review queue need a second person, leave a case record and a Pulse audit entry,
and are idempotent. The AI never sanctions on its own (a human click is always required)."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, MagicMock, patch

from cogs.ai_moderation import AIModeration, CRITICAL_CONFIRM_HOURS


def reviewer(uid):
    return NS(id=uid, display_name=f"Prüfer{uid}")


class SecondConfirmationRuleTests(unittest.TestCase):
    def test_first_click_only_records_the_request(self):
        case = {"id": "C1"}
        message = AIModeration.second_confirmation(case, "ban", reviewer(1))
        self.assertTrue(message)
        self.assertEqual(case["pending_confirmation"]["by"], "1")

    def test_same_person_cannot_confirm_their_own_request(self):
        case = {"id": "C1"}
        AIModeration.second_confirmation(case, "ban", reviewer(1))
        self.assertTrue(AIModeration.second_confirmation(case, "ban", reviewer(1)))
        self.assertIsNotNone(case["pending_confirmation"])

    def test_different_person_confirms(self):
        case = {"id": "C1"}
        AIModeration.second_confirmation(case, "ban", reviewer(1))
        self.assertEqual(AIModeration.second_confirmation(case, "ban", reviewer(2)), "")
        self.assertEqual((case["requested_by"], case["confirmed_by"]), ("1", "2"))
        self.assertIsNone(case["pending_confirmation"])

    def test_other_action_does_not_inherit_the_confirmation(self):
        case = {"id": "C1"}
        AIModeration.second_confirmation(case, "kick", reviewer(1))
        self.assertTrue(AIModeration.second_confirmation(case, "ban", reviewer(2)))

    def test_expired_request_is_replaced_not_confirmed(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=CRITICAL_CONFIRM_HOURS + 1)).isoformat(timespec="seconds")
        case = {"id": "C1", "pending_confirmation": {"action": "ban", "by": "1", "at": old}}
        self.assertTrue(AIModeration.second_confirmation(case, "ban", reviewer(2)))
        self.assertEqual(case["pending_confirmation"]["by"], "2")

    def test_corrupt_timestamp_fails_closed(self):
        case = {"id": "C1", "pending_confirmation": {"action": "ban", "by": "1", "at": "kaputt"}}
        self.assertTrue(AIModeration.second_confirmation(case, "ban", reviewer(2)))


class ReviewFlowTests(unittest.TestCase):
    def tearDown(self):
        import pulse_sanction_guard
        pulse_sanction_guard.reset()

    def setUp(self):
        self.cog = AIModeration.__new__(AIModeration)
        self.cog._action_problem = lambda *a: ""
        self.cog._role_kind = lambda m: "BÜRGER"
        self.cog._save_cases = MagicMock()
        self.cog._case_embed = MagicMock()
        self.cog._apply_citizen_action = AsyncMock(return_value={"ok": True, "message": "gebannt"})
        self.target = NS(id=5, display_name="Ziel", bot=False, guild_permissions=NS(administrator=False))
        self.case = {"id": "C1", "user_id": "5", "status": "open"}

    def click(self, uid, action="ban"):
        user = reviewer(uid)
        interaction = NS(
            user=user, message=NS(edit=AsyncMock()),
            guild=NS(id=1, owner_id=0, get_member=lambda i: self.target, fetch_member=AsyncMock()),
            response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
        with patch("webserver.log_audit") as audit:
            asyncio.run(self.cog._run_review_action(interaction, "C1", self.case, action))
        return interaction, audit

    def test_single_click_never_executes_a_ban(self):
        _, audit = self.click(1)
        self.cog._apply_citizen_action.assert_not_awaited()
        audit.assert_not_called()
        self.assertEqual(self.case["status"], "open")

    def test_second_person_executes_once_with_record_and_audit(self):
        self.click(1)
        _, audit = self.click(2)
        self.cog._apply_citizen_action.assert_awaited_once()
        self.assertEqual(self.case["status"], "resolved")
        self.assertEqual(self.case["actions"][0]["requested_by"], "1")
        self.assertEqual(self.case["actions"][0]["confirmed_by"], "2")
        self.assertEqual(audit.call_count, 1)

    def test_same_person_twice_does_not_execute(self):
        self.click(1)
        self.click(1)
        self.cog._apply_citizen_action.assert_not_awaited()

    def test_timeout_and_warn_stay_single_click(self):
        self.click(1, action="timeout_10m")
        self.cog._apply_citizen_action.assert_awaited_once()

    def test_audit_failure_does_not_undo_the_action(self):
        self.click(1)
        user = reviewer(2)
        interaction = NS(user=user, message=NS(edit=AsyncMock()),
                         guild=NS(id=1, owner_id=0, get_member=lambda i: self.target, fetch_member=AsyncMock()),
                         response=NS(defer=AsyncMock()), followup=NS(send=AsyncMock()))
        with patch("webserver.log_audit", side_effect=RuntimeError("db")):
            asyncio.run(self.cog._run_review_action(interaction, "C1", self.case, "ban"))
        self.assertEqual(self.case["status"], "resolved")


if __name__ == "__main__":
    unittest.main()
