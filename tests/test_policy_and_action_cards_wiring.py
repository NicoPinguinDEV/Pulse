"""Audit #120/#121: neue Policy-Aktionen (Parität zu den bisherigen Prüfungen) und /teamannounce mit Vorschau als Aktionskarte (temp DB, ohne Discord)."""
import asyncio
import itertools
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pulse_db
import pulse_policy as policy
import webserver

KEYS = ("can_manage_announcements", "can_promote", "can_manage_training", "can_manage_wiki", "is_admin")
FORMULAS = {
    "announcement.publish": lambda p: p.get("can_manage_announcements") or p.get("can_promote") or p.get("is_admin"),
    "ack.manage": lambda p: p.get("can_manage_announcements") or p.get("can_promote") or p.get("is_admin"),
    "meeting.lead": lambda p: p.get("can_promote") or p.get("is_admin"),
    "poll.create": lambda p: p.get("can_promote") or p.get("is_admin"),
    "quiz.manage": lambda p: p.get("can_manage_training") or p.get("can_promote") or p.get("is_admin"),
    "wiki.review": lambda p: p.get("can_manage_wiki") or p.get("can_promote") or p.get("is_admin"),
}


class ParityTests(unittest.TestCase):
    def test_new_actions_equal_the_former_inline_checks_for_every_rights_combination(self):
        for combo in itertools.product((False, True), repeat=len(KEYS)):
            perms = {k: v for k, v in zip(KEYS, combo)}
            for action, formula in FORMULAS.items():
                self.assertEqual(policy.can(perms, action), bool(formula(perms)), (action, perms))

    def test_fail_closed_for_garbage_input(self):
        for action in FORMULAS:
            for bad in (None, "is_admin", [], {"is_admin": "yes"}, {"can_promote": 1}):
                self.assertFalse(policy.can(bad, action), (action, bad))
        self.assertFalse(policy.can({"is_admin": True}, "gibtesnicht"))

    def test_modules_use_the_policy(self):
        import pulse_ack_pages
        import pulse_meetings_pages
        import pulse_quiz_admin
        import pulse_wiki_pages
        self.assertTrue(pulse_ack_pages.is_manager({"can_manage_announcements": True}))
        self.assertFalse(pulse_ack_pages.is_manager({"can_view_dashboard": True}))
        self.assertTrue(pulse_meetings_pages.is_lead({"can_promote": True}))
        self.assertFalse(pulse_meetings_pages.is_lead({"can_manage_tasks": True}))
        self.assertTrue(pulse_quiz_admin.can_manage({"can_manage_training": True}))
        self.assertTrue(pulse_wiki_pages.is_manager({"can_manage_wiki": True}))
        self.assertFalse(pulse_wiki_pages.is_manager({}))


class AnnounceBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        patcher.start()
        self.addCleanup(patcher.stop)
        pulse_db.init_db()
        self.perms = {"can_view_dashboard": True, "can_promote": True}
        self.guild = SimpleNamespace(id=1, members=[])
        self.user = SimpleNamespace(id=900000000000000001, display_name="Lead")
        self.send = AsyncMock(return_value=SimpleNamespace(jump_url="https://discord.example/m"))

    def start(self, vorschau):
        from cogs.pulse_pro import PulsePro
        inter = SimpleNamespace(guild=self.guild, user=self.user, response=SimpleNamespace(send_message=AsyncMock()))
        cog = SimpleNamespace(team_members=lambda guild: [], _publish_announcement=lambda *a, **k: PulsePro._publish_announcement(cog, *a, **k))
        with patch.object(webserver, "compute_perms", side_effect=lambda *a: (self.perms, object())), patch.object(webserver, "load_config", return_value={}), \
                patch.object(webserver, "send_team_update_embed", self.send):
            asyncio.run(PulsePro.teamannounce.callback(cog, inter, "Neue Regel", "Bitte lesen", None, False, vorschau))
        return inter, cog


class TeamAnnounceTests(AnnounceBase):
    def test_without_preview_the_behaviour_is_unchanged(self):
        inter, _ = self.start(False)
        self.assertIn("Ankündigung veröffentlicht", inter.response.send_message.await_args.args[0])
        self.assertEqual([a["title"] for a in pulse_db.announcements(10)], ["Neue Regel"])
        self.send.assert_awaited_once()

    def test_preview_publishes_nothing_until_the_owner_confirms(self):
        import pulse_action_cards
        inter, cog = self.start(True)
        kwargs = inter.response.send_message.await_args.kwargs
        self.assertTrue(kwargs["ephemeral"])
        self.assertIn("Neue Regel", kwargs["embed"].title)
        self.assertEqual(pulse_db.announcements(10), [])
        card = kwargs["view"].card
        self.assertIsInstance(card, pulse_action_cards.ActionCard)
        stranger = asyncio.run(card.confirm(900000000000000002))
        self.assertEqual(stranger.state, "denied_owner")
        self.assertEqual(pulse_db.announcements(10), [])
        followup = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
        with patch.object(webserver, "compute_perms", side_effect=lambda *a: (self.perms, object())), patch.object(webserver, "load_config", return_value={}), \
                patch.object(webserver, "send_team_update_embed", self.send):
            outcome = asyncio.run(card.confirm(self.user.id, followup))
        self.assertEqual(outcome.state, "executed")
        self.assertEqual([a["title"] for a in pulse_db.announcements(10)], ["Neue Regel"])
        self.assertIn("veröffentlicht", followup.followup.send.await_args.args[0])
        again = asyncio.run(card.confirm(self.user.id, followup))
        self.assertEqual(again.state, "already_used")
        self.assertEqual(len(pulse_db.announcements(10)), 1)                              # einmalig

    def test_rights_are_checked_again_at_confirmation_and_cancel_publishes_nothing(self):
        inter, _ = self.start(True)
        card = inter.response.send_message.await_args.kwargs["view"].card
        self.perms = {"can_view_dashboard": True}                                         # Recht inzwischen entzogen
        followup = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))
        outcome = asyncio.run(card.confirm(self.user.id, followup))
        self.assertEqual(outcome.state, "denied_rights")
        self.assertEqual(pulse_db.announcements(10), [])

    def test_cancel_and_permission_gate(self):
        inter, _ = self.start(True)
        card = inter.response.send_message.await_args.kwargs["view"].card
        self.assertEqual(asyncio.run(card.cancel(self.user.id)).state, "cancelled")
        self.assertEqual(pulse_db.announcements(10), [])
        self.perms = {"can_view_dashboard": True}
        inter, _ = self.start(True)
        self.assertIn("Berechtigung", inter.response.send_message.await_args.args[0])
        self.assertEqual(pulse_db.announcements(10), [])


if __name__ == "__main__":
    unittest.main()
