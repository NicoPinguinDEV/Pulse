"""Overdue escalation (second stage) and the fresh waiting period of reopened tickets."""
import asyncio
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord

import pulse_db
from pulse_ticket_design import (
    automation_actions,
    automation_settings,
    escalation_state,
    reopen_allowed,
    reopen_settings,
    ticket_activity,
)
from pulse_ticket_triage import triage
from tests.runtime_isolation import setUpModule, tearDownModule  # noqa: F401  (no ./pulse.db)

NOW = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)


def iso(delta):
    return (NOW - delta).isoformat()


SETTINGS = automation_settings({
    "unclaimed_ping_minutes": 30,
    "staff_reply_hours": 12,
    "user_reminder_hours": 24,
    "escalation_minutes": 60,
    "category_sla": {
        "bug": {"escalation_minutes": 0},
        "unban": {"escalation_minutes": 10},
    },
})


class EscalationSettingsTests(unittest.TestCase):
    def test_escalation_is_off_by_default_and_never_fires(self):
        self.assertEqual(automation_settings({})["escalation_minutes"], 0)
        ticket = {"status": "open", "opened_at": iso(timedelta(days=3)),
                  "automation_state": {"unclaimed_ping": iso(timedelta(days=2))}}
        self.assertEqual(automation_actions(ticket, automation_settings({}), NOW), [])

    def test_values_are_clamped_and_category_overrides_are_kept(self):
        settings = automation_settings({
            "escalation_minutes": 999999,
            "category_sla": {"support": {"escalation_minutes": -5}, "../x": {"escalation_minutes": 5}},
        })
        self.assertEqual(settings["escalation_minutes"], 1440)
        self.assertEqual(settings["category_sla"], {"support": {"escalation_minutes": 0}})
        self.assertEqual(automation_settings({"escalation_minutes": "abc"})["escalation_minutes"], 0)


class UnclaimedEscalationTests(unittest.TestCase):
    def ticket(self, pinged_before, **extra):
        return {"status": "open", "category": "support", "opened_at": iso(timedelta(hours=5)),
                "automation_state": {"unclaimed_ping": iso(pinged_before)}, **extra}

    def test_fires_only_after_the_reminder_plus_the_escalation_time(self):
        self.assertNotIn("escalate_unclaimed", automation_actions(self.ticket(timedelta(minutes=59)), SETTINGS, NOW))
        self.assertEqual(automation_actions(self.ticket(timedelta(minutes=60)), SETTINGS, NOW), ["escalate_unclaimed"])

    def test_never_before_the_first_reminder_was_sent(self):
        ticket = {"status": "open", "category": "support", "opened_at": iso(timedelta(hours=5))}
        self.assertEqual(automation_actions(ticket, SETTINGS, NOW), ["remind_unclaimed"])

    def test_fires_once_and_not_for_claimed_or_closed_tickets(self):
        done = self.ticket(timedelta(hours=3))
        done["automation_state"]["unclaimed_escalated"] = iso(timedelta(hours=1))
        self.assertEqual(automation_actions(done, SETTINGS, NOW), [])
        self.assertNotIn("escalate_unclaimed",
                         automation_actions(self.ticket(timedelta(hours=3), claimed_by_id="5"), SETTINGS, NOW))
        self.assertEqual(automation_actions(self.ticket(timedelta(hours=3), status="closed"), SETTINGS, NOW), [])

    def test_category_can_disable_or_shorten_the_escalation(self):
        self.assertEqual(automation_actions(self.ticket(timedelta(hours=9), category="bug"), SETTINGS, NOW), [])
        self.assertEqual(automation_actions(self.ticket(timedelta(minutes=11), category="unban"), SETTINGS, NOW),
                         ["escalate_unclaimed"])

    def test_broken_reminder_timestamp_does_not_escalate(self):
        ticket = self.ticket(timedelta(hours=3))
        ticket["automation_state"] = '{"unclaimed_ping": "x"}'
        self.assertEqual(automation_actions(ticket, SETTINGS, NOW), [])


class StaffEscalationTests(unittest.TestCase):
    def setUp(self):
        self.user_at = NOW - timedelta(hours=20)
        self.ticket = {
            "status": "in_progress", "category": "support", "claimed_by_id": "5",
            "opened_at": iso(timedelta(days=2)),
            "last_staff_message_at": iso(timedelta(hours=30)),
            "last_user_message_at": self.user_at.isoformat(),
        }

    def test_escalates_when_the_reminded_assignee_still_does_not_answer(self):
        self.assertEqual(automation_actions(self.ticket, SETTINGS, NOW), ["remind_staff"])
        self.ticket["automation_state"] = {"staff_reminder_for": self.user_at.isoformat(),
                                           "staff_reminded_at": iso(timedelta(minutes=30))}
        self.assertEqual(automation_actions(self.ticket, SETTINGS, NOW), [])
        self.ticket["automation_state"]["staff_reminded_at"] = iso(timedelta(minutes=61))
        self.assertEqual(automation_actions(self.ticket, SETTINGS, NOW), ["escalate_staff"])
        self.ticket["automation_state"]["staff_escalated_for"] = self.user_at.isoformat()
        self.assertEqual(automation_actions(self.ticket, SETTINGS, NOW), [])

    def test_reminders_from_before_the_feature_are_not_escalated(self):
        self.ticket["automation_state"] = {"staff_reminder_for": self.user_at.isoformat()}
        self.assertEqual(automation_actions(self.ticket, SETTINGS, NOW), [])

    def test_a_team_answer_ends_the_situation(self):
        self.ticket["automation_state"] = {"staff_reminder_for": self.user_at.isoformat(),
                                           "staff_reminded_at": iso(timedelta(hours=5))}
        self.ticket["last_staff_message_at"] = iso(timedelta(hours=1))
        self.assertNotIn("escalate_staff", automation_actions(self.ticket, SETTINGS, NOW))

    def test_a_new_creator_message_needs_its_own_reminder_first(self):
        self.ticket["automation_state"] = {"staff_reminder_for": self.user_at.isoformat(),
                                           "staff_reminded_at": iso(timedelta(hours=5)),
                                           "staff_escalated_for": self.user_at.isoformat()}
        self.ticket["last_user_message_at"] = iso(timedelta(hours=1))
        self.assertEqual(automation_actions(self.ticket, SETTINGS, NOW), [])
        self.assertEqual(escalation_state(self.ticket), "")

    def test_triage_marks_escalated_tickets(self):
        self.assertFalse(triage(self.ticket, NOW, SETTINGS)["escalated"])
        self.ticket["automation_state"] = {"staff_escalated_for": self.user_at.isoformat()}
        self.assertTrue(triage(self.ticket, NOW, SETTINGS)["escalated"])
        unclaimed = {"status": "open", "opened_at": iso(timedelta(hours=4)),
                     "automation_state": {"unclaimed_escalated": iso(timedelta(hours=1))}}
        self.assertTrue(triage(unclaimed, NOW, SETTINGS)["escalated"])
        self.assertFalse(triage({**unclaimed, "status": "closed"}, NOW, SETTINGS)["escalated"])


class ReopenedTicketTests(unittest.TestCase):
    def test_old_activity_does_not_make_a_reopened_ticket_overdue(self):
        ticket = {
            "status": "open", "category": "support", "opened_at": iso(timedelta(days=40)),
            "last_user_message_at": iso(timedelta(days=39)), "last_staff_message_at": iso(timedelta(days=38)),
            "automation_state": {"reopened_at": iso(timedelta(minutes=5)), "reopen_count": 1},
        }
        opened, last_user, last_staff = ticket_activity(ticket)
        self.assertEqual(opened, NOW - timedelta(minutes=5))
        self.assertIsNone(last_user)
        self.assertIsNone(last_staff)
        self.assertEqual(automation_actions(ticket, SETTINGS, NOW), [])
        info = triage(ticket, NOW, SETTINGS)
        self.assertEqual(info["state"], "new")
        self.assertFalse(info["overdue"])

    def test_reopened_ticket_is_reminded_again_after_its_own_wait(self):
        ticket = {"status": "open", "category": "support", "opened_at": iso(timedelta(days=40)),
                  "automation_state": {"reopened_at": iso(timedelta(minutes=31))}}
        self.assertEqual(automation_actions(ticket, SETTINGS, NOW), ["remind_unclaimed"])
        self.assertTrue(triage(ticket, NOW, SETTINGS)["overdue"])

    def test_messages_after_the_reopening_count(self):
        ticket = {"status": "in_progress", "claimed_by_id": "5", "opened_at": iso(timedelta(days=40)),
                  "last_user_message_at": iso(timedelta(hours=13)),
                  "automation_state": {"reopened_at": iso(timedelta(hours=20))}}
        self.assertEqual(automation_actions(ticket, SETTINGS, NOW), ["remind_staff"])

    def test_tickets_without_reopening_are_unchanged(self):
        ticket = {"status": "open", "opened_at": iso(timedelta(minutes=45))}
        self.assertEqual(ticket_activity(ticket), (NOW - timedelta(minutes=45), None, None))


class ReopenWindowTests(unittest.TestCase):
    def closed(self, days):
        return {"status": "closed", "closed_at": iso(timedelta(days=days))}

    def test_settings_default_to_unlimited_and_are_clamped(self):
        self.assertEqual(reopen_settings(None), {"window_days": 0})
        self.assertEqual(reopen_settings({"window_days": 9999}), {"window_days": 365})
        self.assertEqual(reopen_settings({"window_days": -3}), {"window_days": 0})
        self.assertEqual(reopen_settings({"window_days": "x"}), {"window_days": 0})

    def test_unlimited_window_keeps_the_previous_behaviour(self):
        self.assertEqual(reopen_allowed(self.closed(900), None, NOW), (True, ""))

    def test_window_is_enforced_for_non_admins(self):
        settings = {"window_days": 14}
        self.assertTrue(reopen_allowed(self.closed(13), settings, NOW)[0])
        allowed, reason = reopen_allowed(self.closed(15), settings, NOW)
        self.assertFalse(allowed)
        self.assertIn("14 Tagen", reason)
        self.assertTrue(reopen_allowed(self.closed(15), settings, NOW, is_admin=True)[0])

    def test_missing_closing_time_is_denied_by_default(self):
        ticket = {"status": "closed", "closed_at": None}
        self.assertFalse(reopen_allowed(ticket, {"window_days": 14}, NOW)[0])
        self.assertTrue(reopen_allowed(ticket, {"window_days": 14}, NOW, is_admin=True)[0])
        self.assertTrue(reopen_allowed(ticket, {"window_days": 0}, NOW)[0])

    def test_open_tickets_can_never_be_reopened(self):
        self.assertFalse(reopen_allowed({"status": "open"}, None, NOW, is_admin=True)[0])


class DatabaseTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()
        self.ticket_id = pulse_db.create_ticket(channel_id=555, guild_id=1, user_id=789,
                                                user_name="Ersteller", category="support")

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()


class ReopenDatabaseTests(DatabaseTestCase):
    def test_reopening_starts_a_fresh_automation_state_and_counts(self):
        pulse_db.set_ticket_automation_state(self.ticket_id, {"unclaimed_ping": "2026-01-01T00:00:00+00:00",
                                                              "unclaimed_escalated": "2026-01-01T02:00:00+00:00"})
        self.assertTrue(pulse_db.close_ticket(self.ticket_id, "Erledigt", "", 90, "Bearbeiter"))
        self.assertTrue(pulse_db.reopen_ticket(self.ticket_id, 888, 90, "Bearbeiter"))
        state = json.loads(pulse_db.get_ticket(ticket_id=self.ticket_id)["automation_state"])
        self.assertEqual(set(state), {"reopened_at", "reopen_count"})
        self.assertEqual(state["reopen_count"], 1)

        self.assertTrue(pulse_db.close_ticket(self.ticket_id, "Erledigt", "", 90, "Bearbeiter"))
        self.assertTrue(pulse_db.reopen_ticket(self.ticket_id, 889, 90, "Bearbeiter"))
        state = json.loads(pulse_db.get_ticket(ticket_id=self.ticket_id)["automation_state"])
        self.assertEqual(state["reopen_count"], 2)

    def test_reopening_an_open_ticket_changes_nothing(self):
        pulse_db.set_ticket_automation_state(self.ticket_id, {"unclaimed_ping": "x"})
        self.assertFalse(pulse_db.reopen_ticket(self.ticket_id, 888, 90, "Bearbeiter"))
        ticket = pulse_db.get_ticket(ticket_id=self.ticket_id)
        self.assertEqual(json.loads(ticket["automation_state"]), {"unclaimed_ping": "x"})
        self.assertEqual(ticket["channel_id"], "555")

    def test_broken_stored_state_does_not_block_reopening(self):
        with pulse_db.connect() as db:
            db.execute("UPDATE tickets SET automation_state='kaputt' WHERE id=?", (self.ticket_id,))
        self.assertTrue(pulse_db.close_ticket(self.ticket_id, "Erledigt", "", 90, "Bearbeiter"))
        self.assertTrue(pulse_db.reopen_ticket(self.ticket_id, 888, 90, "Bearbeiter"))
        self.assertEqual(json.loads(pulse_db.get_ticket(ticket_id=self.ticket_id)["automation_state"])["reopen_count"], 1)

    def test_events_are_only_added_to_existing_tickets(self):
        self.assertFalse(pulse_db.add_ticket_event("gibt-es-nicht", "escalated", "x"))
        self.assertTrue(pulse_db.add_ticket_event(self.ticket_id, "escalated", "x" * 900))
        event = next(e for e in pulse_db.ticket_events(self.ticket_id) if e["event_type"] == "escalated")
        self.assertEqual(len(event["details"]), 500)


def _role(role_id, members, managed=False):
    role = MagicMock()
    role.id = role_id
    role.managed = managed
    role.mention = f"<@&{role_id}>"
    role.members = members
    role.is_default.return_value = False
    return role


class EscalationExecutorTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        import cogs.tickets as tickets
        self.tickets = tickets
        self.lead = SimpleNamespace(id=10, bot=False)
        self.viewer = SimpleNamespace(id=11, bot=False)
        self.robot = SimpleNamespace(id=12, bot=True)
        self.role = _role(700, [self.lead, self.viewer, self.robot])
        self.managed = _role(701, [SimpleNamespace(id=13, bot=False)], managed=True)
        self.guild = MagicMock()
        self.guild.id = 1
        self.guild.get_role.side_effect = lambda rid: {700: self.role, 701: self.managed}.get(rid)
        self.log_channel = MagicMock(spec=discord.TextChannel)
        self.log_channel.guild = self.guild
        self.log_channel.send = AsyncMock()
        self.guild.get_channel.side_effect = lambda cid: self.log_channel if cid == 4242 else None
        self.bot = SimpleNamespace(fetch_channel=AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "x")))
        self.ticket = pulse_db.get_ticket(ticket_id=self.ticket_id)
        perms = {10: {"can_manage_tickets": True}, 11: {"can_view_dashboard": True}, 13: {"is_admin": True}}
        self.patches = [
            patch.object(tickets, "ticket_config", return_value={
                "leadership_role_ids": ["700", "701", "kaputt", 700], "ticket_claim_log_channel_id": 4242}),
            patch("webserver.load_config", return_value={}),
            patch("webserver.compute_perms", side_effect=lambda _guild, member_id, _config: (perms.get(member_id, {}), None)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        super().tearDown()

    def run_escalation(self, kind="unclaimed"):
        return asyncio.run(self.tickets.escalate_overdue_ticket(self.bot, self.guild, self.ticket, kind, 60))

    def notifications(self, user_id):
        with pulse_db.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM notifications WHERE user_id=?", (str(user_id),)).fetchall()]

    def test_only_authorised_leadership_members_are_notified(self):
        result = self.run_escalation()
        self.assertEqual(result, {"notified": 1, "logged": True})
        self.assertEqual(len(self.notifications(10)), 1)
        self.assertIn(self.ticket_id, self.notifications(10)[0]["body"])
        for outsider in (11, 12, 13):  # no ticket right, bot, member of a managed role
            self.assertEqual(self.notifications(outsider), [])

    def test_log_post_pings_only_the_leadership_roles(self):
        self.run_escalation("staff")
        kwargs = self.log_channel.send.await_args.kwargs
        self.assertEqual(kwargs["content"], "<@&700>")
        mentions = kwargs["allowed_mentions"]
        self.assertEqual(mentions.roles, [self.role])
        self.assertFalse(mentions.users)
        self.assertFalse(mentions.everyone)
        self.assertIn("Eskalation", kwargs["embed"].title)

    def test_history_and_audit_are_written_and_the_ticket_is_untouched(self):
        before = pulse_db.get_ticket(ticket_id=self.ticket_id)
        self.run_escalation()
        after = pulse_db.get_ticket(ticket_id=self.ticket_id)
        self.assertEqual(before, after)
        self.assertTrue(any(e["event_type"] == "escalated" for e in pulse_db.ticket_events(self.ticket_id)))
        with pulse_db.connect() as db:
            audit = db.execute("SELECT COUNT(*) FROM events WHERE event_type='ticket_escalated' AND target_id=?",
                               (self.ticket_id,)).fetchone()[0]
        self.assertEqual(audit, 1)

    def test_missing_log_channel_still_reaches_the_inbox(self):
        self.guild.get_channel.side_effect = lambda _cid: None
        self.assertEqual(self.run_escalation(), {"notified": 1, "logged": False})
        self.assertEqual(len(self.notifications(10)), 1)

    def test_log_channel_of_another_guild_is_never_used(self):
        self.log_channel.guild = MagicMock(id=2)
        self.assertEqual(self.run_escalation()["logged"], False)
        self.log_channel.send.assert_not_awaited()

    def test_discord_refusal_is_reported_not_raised(self):
        self.log_channel.send.side_effect = discord.Forbidden(MagicMock(status=403), "x")
        self.assertEqual(self.run_escalation(), {"notified": 1, "logged": False})

    def test_repeated_escalation_does_not_duplicate_the_inbox_entry(self):
        self.run_escalation()
        self.run_escalation()
        self.assertEqual(len(self.notifications(10)), 1)


class AutomationLoopTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        import cogs.tickets as tickets
        self.tickets = tickets
        self.channel = MagicMock(spec=discord.TextChannel)
        self.channel.id = 555
        self.channel.send = AsyncMock()
        self.guild = MagicMock()
        self.guild.id = 1
        self.guild.get_channel.return_value = self.channel
        self.cog = SimpleNamespace(bot=SimpleNamespace(get_guild=lambda _gid: self.guild), ticket_channel_ids=set())
        self.now = datetime.now(timezone.utc)
        self.config = {"automation": {"unclaimed_ping_minutes": 30, "staff_reply_hours": 0,
                                      "user_reminder_hours": 0, "escalation_minutes": 60}}
        self.config_patch = patch.object(tickets, "ticket_config", side_effect=lambda: self.config)
        self.config_patch.start()

    def tearDown(self):
        self.config_patch.stop()
        super().tearDown()

    def state(self):
        return json.loads(pulse_db.get_ticket(ticket_id=self.ticket_id)["automation_state"] or "{}")

    def run_loop(self, escalate):
        with patch.object(self.tickets, "escalate_overdue_ticket", escalate):
            asyncio.run(self.tickets.Tickets.run_automation(self.cog))

    def test_escalation_runs_once_and_is_marked_before_discord_is_called(self):
        pinged = (self.now - timedelta(hours=2)).isoformat()
        pulse_db.set_ticket_automation_state(self.ticket_id, {"unclaimed_ping": pinged})
        seen_state = {}

        async def escalate(_bot, guild, ticket, kind, minutes):
            seen_state.update(self.state())
            self.assertIs(guild, self.guild)
            self.assertEqual((ticket["id"], kind, minutes), (self.ticket_id, "unclaimed", 60))
            raise RuntimeError("Discord nicht erreichbar")

        self.run_loop(escalate)
        self.assertIn("unclaimed_escalated", seen_state)
        self.assertIn("unclaimed_escalated", self.state())
        second = AsyncMock()
        self.run_loop(second)
        second.assert_not_awaited()

    def test_disabled_escalation_never_calls_the_executor(self):
        self.config["automation"]["escalation_minutes"] = 0
        pulse_db.set_ticket_automation_state(self.ticket_id, {"unclaimed_ping": (self.now - timedelta(days=2)).isoformat()})
        escalate = AsyncMock()
        self.run_loop(escalate)
        escalate.assert_not_awaited()
        self.assertNotIn("unclaimed_escalated", self.state())

    def test_staff_reminder_stores_its_time_so_it_can_escalate_later(self):
        self.config["automation"].update({"staff_reply_hours": 1, "unclaimed_ping_minutes": 0})
        pulse_db.claim_ticket(self.ticket_id, 5, "Bearbeiter")
        user_at = (self.now - timedelta(hours=3)).isoformat()
        with pulse_db.connect() as db:
            db.execute("UPDATE tickets SET last_user_message_at=? WHERE id=?", (user_at, self.ticket_id))
        self.guild.get_member.return_value = SimpleNamespace(id=5, mention="<@5>")
        escalate = AsyncMock()
        self.run_loop(escalate)
        escalate.assert_not_awaited()
        self.channel.send.assert_awaited_once()
        state = self.state()
        self.assertIn("staff_reminded_at", state)

        state["staff_reminded_at"] = (self.now - timedelta(minutes=61)).isoformat()
        pulse_db.set_ticket_automation_state(self.ticket_id, state)
        self.run_loop(escalate)
        escalate.assert_awaited_once()
        self.assertEqual(escalate.await_args.args[3], "staff")
        self.assertEqual(self.state()["staff_escalated_for"], state["staff_reminder_for"])


if __name__ == "__main__":
    unittest.main()
