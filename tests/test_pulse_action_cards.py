"""Audit #121: shared action card (owner-only, single use, expiry, re-check of rights, audit, Discord buttons)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pulse_action_cards as cards

OWNER = 1001
OTHER = 2002
ADMIN = {"is_admin": True}
TICKET = {"can_manage_tickets": True}


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def make_card(perms=TICKET, run=None, ttl=60, clock=None, **kw):
    log = []
    card = cards.ActionCard(
        owner_id=OWNER, action="ticket.manage", title="Ticket schließen", run=run or AsyncMock(),
        perms_provider=(perms if callable(perms) else (lambda: perms)), domain="ticket", action_name="close",
        target_type="ticket", target_id="T1", ttl=ttl, clock=clock or Clock(),
        emitter=lambda domain, action, **k: log.append((domain, action, k)), **kw)
    return card, log


class CardTests(unittest.IsolatedAsyncioTestCase):
    async def test_owner_confirms_and_action_runs_once(self):
        run = AsyncMock()
        card, log = make_card(run=run)
        out = await card.confirm(OWNER, "interaction")
        self.assertEqual((out.state, out.consumed), ("executed", True))
        run.assert_awaited_once_with("interaction")
        self.assertEqual([(d, a) for d, a, _ in log], [("ticket", "close_confirmed")])
        again = await card.confirm(OWNER)
        self.assertEqual(again.state, "already_used")
        run.assert_awaited_once()

    async def test_other_person_cannot_confirm_or_cancel_and_card_stays_usable(self):
        run = AsyncMock()
        card, log = make_card(run=run)
        self.assertEqual((await card.confirm(OTHER)).state, "denied_owner")
        self.assertEqual((await card.cancel(OTHER)).state, "denied_owner")
        self.assertFalse(card.used)
        run.assert_not_awaited()
        self.assertEqual(log, [])
        self.assertEqual((await card.confirm(OWNER)).state, "executed")

    async def test_double_click_while_running_never_runs_twice(self):
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def slow(_):
            calls.append(1)
            started.set()
            await release.wait()
        card, _ = make_card(run=slow)
        first = asyncio.create_task(card.confirm(OWNER))
        await started.wait()
        second = await card.confirm(OWNER)
        self.assertEqual(second.state, "already_used")
        self.assertFalse(second.consumed)  # must not overwrite the running message
        release.set()
        await first
        self.assertEqual(calls, [1])

    async def test_expired_card_does_nothing(self):
        clock = Clock()
        run = AsyncMock()
        card, log = make_card(run=run, ttl=30, clock=clock)
        clock.t = 31
        out = await card.confirm(OWNER)
        self.assertEqual(out.state, "expired")
        run.assert_not_awaited()
        self.assertEqual(log[0][1], "close_expired")
        self.assertEqual((await card.confirm(OWNER)).state, "already_used")

    async def test_rights_are_rechecked_at_confirmation(self):
        state = {"perms": TICKET}
        run = AsyncMock()
        card, log = make_card(perms=lambda: state["perms"], run=run)
        state["perms"] = {"can_view_dashboard": True}  # role removed while the card was open
        out = await card.confirm(OWNER)
        self.assertEqual(out.state, "denied_rights")
        run.assert_not_awaited()
        self.assertEqual(log[0][1], "close_denied")

    async def test_broken_permission_lookup_denies(self):
        def boom():
            raise RuntimeError("discord weg")
        card, _ = make_card(perms=boom)
        with self.assertLogs("pulse.actions", level="WARNING"):
            self.assertEqual((await card.confirm(OWNER)).state, "denied_rights")

    async def test_failing_action_reports_error_id_without_details(self):
        async def bad(_):
            raise RuntimeError("geheime Details / Token abc")
        card, log = make_card(run=bad)
        with self.assertLogs("pulse.actions", level="ERROR"):
            out = await card.confirm(OWNER)
        self.assertEqual(out.state, "failed")
        self.assertRegex(out.error_id, r"^[0-9A-F]{6}$")
        self.assertIn(out.error_id, out.message)
        self.assertNotIn("geheime", out.message)
        self.assertNotIn("geheime", str(log))
        self.assertEqual(log[0][1], "close_failed")

    async def test_cancel_is_audited_and_prevents_run(self):
        run = AsyncMock()
        card, log = make_card(run=run)
        self.assertEqual((await card.cancel(OWNER)).state, "cancelled")
        self.assertEqual((await card.confirm(OWNER)).state, "already_used")
        run.assert_not_awaited()
        self.assertEqual([a for _, a, _ in log], ["close_cancelled"])

    async def test_unknown_policy_action_is_rejected_at_creation(self):
        with self.assertRaises(ValueError):
            cards.ActionCard(owner_id=1, action="nope", title="t", run=AsyncMock(), perms_provider=lambda: ADMIN)

    async def test_audit_carries_case_ref_and_policy_action(self):
        card, log = make_card(case_ref="BM:MOD-1A2B3C")
        await card.confirm(OWNER)
        _, _, kwargs = log[0]
        self.assertEqual(kwargs["case_ref"], "BM:MOD-1A2B3C")
        self.assertEqual(kwargs["payload"]["policy_action"], "ticket.manage")


def fake_interaction(user_id):
    """Like Discord: once the interaction was answered, ``is_done()`` is True."""
    answered = {"done": False}

    async def answer(*a, **k):
        answered["done"] = True
    response = SimpleNamespace(is_done=lambda: answered["done"], edit_message=AsyncMock(side_effect=answer),
                               send_message=AsyncMock(side_effect=answer))
    return SimpleNamespace(user=SimpleNamespace(id=user_id), response=response, edit_original_response=AsyncMock(),
                           data={"custom_id": "x"})


class ViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_confirm_button_runs_action_and_replaces_message(self):
        run = AsyncMock()
        card, _ = make_card(run=run)
        view = cards.ActionCardView(card)
        i = fake_interaction(OWNER)
        await view.confirm_button.callback(i)
        run.assert_awaited_once()
        i.response.edit_message.assert_awaited_once()
        self.assertIn("Wird ausgeführt", i.response.edit_message.await_args.kwargs["content"])
        self.assertIn("Erledigt", i.edit_original_response.await_args.kwargs["content"])
        self.assertTrue(view.is_finished())

    async def test_foreign_click_gets_private_notice_and_card_survives(self):
        run = AsyncMock()
        card, _ = make_card(run=run)
        view = cards.ActionCardView(card)
        i = fake_interaction(OTHER)
        await view.confirm_button.callback(i)
        i.response.send_message.assert_awaited_once()
        self.assertTrue(i.response.send_message.await_args.kwargs["ephemeral"])
        i.response.edit_message.assert_not_awaited()
        run.assert_not_awaited()
        self.assertFalse(view.is_finished())

    async def test_cancel_button(self):
        card, _ = make_card()
        view = cards.ActionCardView(card)
        i = fake_interaction(OWNER)
        await view.cancel_button.callback(i)
        self.assertIn("Abgebrochen", i.response.edit_message.await_args.kwargs["content"])
        self.assertTrue(card.used)
        self.assertTrue(view.is_finished())

    async def test_rights_lost_shows_denial_in_message(self):
        card, _ = make_card(perms={})
        view = cards.ActionCardView(card)
        i = fake_interaction(OWNER)
        await view.confirm_button.callback(i)
        self.assertIn("Berechtigung", i.edit_original_response.await_args.kwargs["content"])


if __name__ == "__main__":
    unittest.main()
