"""End-to-end dashboard ticket claim and close workflow with a temporary SQLite database."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import pulse_db
import webserver as ws


class TicketWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(pulse_db, "DB_PATH", str(Path(self.tmp.name) / "pulse.db"))
        self.db_patch.start()
        pulse_db.init_db()

        self.channel = SimpleNamespace(id=555, sent=[], deleted=False)

        async def send(content=None, **kwargs):
            self.channel.sent.append((content, kwargs))

        async def delete(**_kwargs):
            self.channel.deleted = True

        self.channel.send = send
        self.channel.delete = delete
        self.member = SimpleNamespace(id=123, display_name="Support", mention="<@123>")
        self.guild = SimpleNamespace(
            id=1,
            get_channel=lambda channel_id: self.channel if channel_id == self.channel.id else None,
            get_member=lambda _user_id: None,
        )
        self.perms = dict({key: True for key in ws.PERM_KEYS}, is_admin=True, can_manage_tickets=True)
        self.auth_patch = patch.object(ws, "auth", self._fake_auth)
        self.auth_patch.start()
        self.client = TestClient(ws.app, raise_server_exceptions=False)

    def tearDown(self):
        self.auth_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def _fake_auth(self, request, session, perm="can_view_dashboard", admin=False):
        from types import SimpleNamespace
        from fastapi import HTTPException

        if admin and not self.perms.get("is_admin"):
            raise HTTPException(status_code=403, detail="Nur Admins.")
        if perm and not self.perms.get(perm):
            raise HTTPException(status_code=403, detail="Berechtigung fehlt.")
        return SimpleNamespace(
            guild=self.guild,
            member=self.member,
            user={"id": str(self.member.id), "global_name": self.member.display_name},
            perms=self.perms,
        )

    def test_ticket_is_claimed_then_archived_and_closed(self):
        ticket_id = pulse_db.create_ticket(
            channel_id=self.channel.id,
            guild_id=self.guild.id,
            user_id=789,
            user_name="Ersteller",
            category="support",
        )
        bot = SimpleNamespace(is_ready=lambda: True)
        self.client.app.state.bot = bot

        with patch.object(ws, "_send_pulse_ticket_log", new_callable=AsyncMock), \
                patch.object(ws, "_refresh_pulse_ticket_discord_state", new_callable=AsyncMock), \
                patch("pulse_transcripts.archive_transcript", new_callable=AsyncMock, return_value=f"{ticket_id}.txt"), \
                patch.object(ws.discord, "TextChannel", type(self.channel)):
            claimed = self.client.post(
                "/tickets/action",
                data={"ticket_id": ticket_id, "action": "claim"},
                follow_redirects=False,
            )
            self.assertEqual(claimed.status_code, 303)
            self.assertIn("wurde übernommen", unquote(claimed.headers["location"]))
            current = pulse_db.get_ticket(ticket_id=ticket_id)
            self.assertEqual(current["status"], "in_progress")
            self.assertEqual(current["claimed_by_id"], str(self.member.id))

            closed = self.client.post(
                "/tickets/action",
                data={"ticket_id": ticket_id, "action": "close", "reason": "Anliegen gelöst"},
                follow_redirects=False,
            )
            self.assertEqual(closed.status_code, 303)
            self.assertIn("wurde geschlossen", unquote(closed.headers["location"]))

        final = pulse_db.get_ticket(ticket_id=ticket_id)
        self.assertEqual(final["status"], "closed")
        self.assertEqual(final["close_reason"], "Anliegen gelöst")
        self.assertEqual(final["transcript_path"], f"{ticket_id}.txt")
        self.assertEqual(final["closed_by_id"], str(self.member.id))
        self.assertTrue(self.channel.deleted)

    def test_close_requires_a_reason_and_keeps_ticket_open(self):
        ticket_id = pulse_db.create_ticket(
            channel_id=self.channel.id,
            guild_id=self.guild.id,
            user_id=789,
            user_name="Ersteller",
            category="support",
        )
        self.client.app.state.bot = SimpleNamespace(is_ready=lambda: True)

        with patch.object(ws.discord, "TextChannel", type(self.channel)):
            response = self.client.post(
                "/tickets/action",
                data={"ticket_id": ticket_id, "action": "close", "reason": ""},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn("Grund erforderlich", unquote(response.headers["location"]))
        self.assertEqual(pulse_db.get_ticket(ticket_id=ticket_id)["status"], "open")
        self.assertFalse(self.channel.deleted)

    def _prepare_closed_ticket_reopen(self):
        ticket_id = pulse_db.create_ticket(
            channel_id=self.channel.id,
            guild_id=self.guild.id,
            user_id=789,
            user_name="Ersteller",
            category="support",
        )
        self.assertTrue(pulse_db.close_ticket(ticket_id, "Erledigt", f"{ticket_id}.txt", 90, "Alter Bearbeiter"))

        owner = type("TicketOwner", (), {})()
        owner.id = 789
        owner.name = "Ersteller"
        owner.display_name = "Ersteller"
        self.guild.default_role = object()
        self.guild.me = object()
        self.guild.categories = []
        self.guild.get_member = lambda user_id: owner if user_id == owner.id else None
        self.guild.create_category = AsyncMock(return_value=SimpleNamespace(name="🎫・Tickets"))
        created_channel = SimpleNamespace(id=888, delete=AsyncMock())
        self.guild.create_text_channel = AsyncMock(return_value=created_channel)
        self.client.app.state.bot = SimpleNamespace(
            is_ready=lambda: True,
            fetch_channel=AsyncMock(return_value=None),
        )
        return ticket_id, owner, created_channel

    def test_closed_ticket_reopens_in_a_private_new_channel(self):
        ticket_id, owner, created_channel = self._prepare_closed_ticket_reopen()
        with (
            patch("cogs.tickets.category_roles", return_value=[]),
            patch("cogs.tickets.ticket_category_definitions", return_value={"support": {}}),
            patch("cogs.tickets.category_channel_name", return_value="ticket-reopened"),
            patch("cogs.tickets.category_meta", return_value=("Support", "", "🎫", 0, "team")),
            patch("cogs.tickets.TicketControlView", return_value=object()),
            patch.object(ws, "send_styled", new_callable=AsyncMock),
            patch.object(ws, "_send_pulse_ticket_log", new_callable=AsyncMock),
        ):
            response = self.client.post(
                "/tickets/action",
                data={"ticket_id": ticket_id, "action": "reopen"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn("wurde wieder geöffnet", unquote(response.headers["location"]))
        latest = pulse_db.get_ticket(ticket_id=ticket_id)
        self.assertEqual(latest["status"], "open")
        self.assertEqual(latest["channel_id"], str(created_channel.id))
        self.assertIsNone(latest["claimed_by_id"])
        self.assertEqual(latest["transcript_path"], f"{ticket_id}.txt")
        self.assertEqual(latest["close_reason"], "")
        self.assertTrue(
            any(event["event_type"] == "reopened" for event in pulse_db.ticket_events(ticket_id))
        )
        overwrites = self.guild.create_text_channel.await_args.kwargs["overwrites"]
        self.assertIn(owner, overwrites)
        self.assertFalse(overwrites[self.guild.default_role].view_channel)
        self.assertTrue(overwrites[owner].view_channel)

    def test_reopen_database_race_deletes_the_new_channel_and_keeps_ticket_closed(self):
        ticket_id, _owner, created_channel = self._prepare_closed_ticket_reopen()
        with (
            patch("cogs.tickets.category_roles", return_value=[]),
            patch("cogs.tickets.ticket_category_definitions", return_value={"support": {}}),
            patch("cogs.tickets.category_channel_name", return_value="ticket-reopened"),
            patch.object(ws.pulse_db, "reopen_ticket", return_value=False),
        ):
            response = self.client.post(
                "/tickets/action",
                data={"ticket_id": ticket_id, "action": "reopen"},
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 303)
        self.assertIn("konnte nicht wieder geöffnet werden", unquote(response.headers["location"]))
        self.assertTrue(created_channel.delete.await_count == 1)
        self.assertEqual(pulse_db.get_ticket(ticket_id=ticket_id)["status"], "closed")

    def test_reopen_requires_ticket_management_permission(self):
        ticket_id, _owner, created_channel = self._prepare_closed_ticket_reopen()
        self.perms["can_manage_tickets"] = False
        response = self.client.post(
            "/tickets/action",
            data={"ticket_id": ticket_id, "action": "reopen"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(pulse_db.get_ticket(ticket_id=ticket_id)["status"], "closed")
        self.guild.create_text_channel.assert_not_awaited()
        created_channel.delete.assert_not_awaited()

    def test_auto_assignment_chooses_least_loaded_category_authorised_staff(self):
        from cogs import tickets as ticket_cog

        role = SimpleNamespace(id=40)
        busy = SimpleNamespace(
            id=10, display_name="Voll", name="voll", bot=False, roles=[role],
        )
        available = SimpleNamespace(
            id=20, display_name="Frei", name="frei", bot=False, roles=[role],
        )
        no_permission = SimpleNamespace(
            id=30, display_name="Ohne Recht", name="ohne-recht", bot=False, roles=[role],
        )
        bot_member = SimpleNamespace(
            id=40, display_name="Bot", name="bot", bot=True, roles=[role],
        )
        members = {m.id: m for m in (busy, available, no_permission, bot_member)}
        self.guild.members = list(members.values())
        self.guild.get_member = lambda member_id: members.get(member_id)

        open_tickets = [
            {"guild_id": "1", "status": "open", "claimed_by_id": "10", "priority": "urgent"},
            {"guild_id": "1", "status": "in_progress", "claimed_by_id": "10", "priority": "normal"},
            {"guild_id": "1", "status": "open", "claimed_by_id": "20", "priority": "low"},
        ]
        with (
            patch.object(ticket_cog, "ticket_config", return_value={
                "distribution": {"enabled": True, "max_open_per_staff": 2},
            }),
            patch.object(ticket_cog, "category_roles", return_value=[role]),
            patch.object(ticket_cog, "list_tickets", return_value=open_tickets),
            patch.object(ws, "load_config", return_value={}),
            patch.object(
                ws,
                "compute_perms",
                side_effect=lambda _guild, member_id, _config: (
                    {"can_manage_tickets": member_id in {10, 20}, "is_admin": False},
                    {},
                ),
            ),
        ):
            selected = ticket_cog.choose_ticket_auto_assignee(self.guild, "support")

        self.assertIs(selected, available)

    def test_auto_assignment_is_opt_in_and_keeps_disabled_flow_unassigned(self):
        from cogs import tickets as ticket_cog

        with (
            patch.object(ticket_cog, "ticket_config", return_value={}),
            patch.object(ticket_cog, "category_roles") as roles,
        ):
            selected = ticket_cog.choose_ticket_auto_assignee(self.guild, "support")
        self.assertIsNone(selected)
        roles.assert_not_called()

    def _reopen(self, ticket_id):
        with (
            patch("cogs.tickets.category_roles", return_value=[]),
            patch("cogs.tickets.ticket_category_definitions", return_value={"support": {}}),
            patch("cogs.tickets.category_channel_name", return_value="ticket-reopened"),
            patch("cogs.tickets.category_meta", return_value=("Support", "", "🎫", 0, "team")),
            patch("cogs.tickets.TicketControlView", return_value=object()),
            patch.object(ws, "send_styled", new_callable=AsyncMock),
            patch.object(ws, "_send_pulse_ticket_log", new_callable=AsyncMock),
        ):
            return self.client.post(
                "/tickets/action",
                data={"ticket_id": ticket_id, "action": "reopen"},
                follow_redirects=False,
            )

    def _close_long_ago(self, ticket_id, days):
        from datetime import datetime, timedelta, timezone
        closed_at = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
        with pulse_db.connect() as db:
            db.execute("UPDATE tickets SET closed_at=? WHERE id=?", (closed_at, ticket_id))

    def test_reopen_window_is_enforced_on_the_server_for_ticket_managers(self):
        ticket_id, _owner, created_channel = self._prepare_closed_ticket_reopen()
        pulse_db.set_setting("ticket_system_config", {"reopen": {"window_days": 7}})
        self._close_long_ago(ticket_id, 8)
        self.perms["is_admin"] = False

        response = self._reopen(ticket_id)

        self.assertEqual(response.status_code, 303)
        self.assertIn("Frist von 7 Tagen", unquote(response.headers["location"]))
        self.assertEqual(pulse_db.get_ticket(ticket_id=ticket_id)["status"], "closed")
        self.guild.create_text_channel.assert_not_awaited()
        created_channel.delete.assert_not_awaited()

    def test_reopen_inside_the_window_and_admin_override_still_work(self):
        ticket_id, _owner, _channel = self._prepare_closed_ticket_reopen()
        pulse_db.set_setting("ticket_system_config", {"reopen": {"window_days": 7}})
        self._close_long_ago(ticket_id, 6)
        self.perms["is_admin"] = False
        self.assertIn("wurde wieder geöffnet", unquote(self._reopen(ticket_id).headers["location"]))
        self.assertEqual(pulse_db.get_ticket(ticket_id=ticket_id)["status"], "open")

        self.assertTrue(pulse_db.close_ticket(ticket_id, "Erledigt", f"{ticket_id}.txt", 90, "Bearbeiter"))
        self._close_long_ago(ticket_id, 400)
        self.perms["is_admin"] = True
        self.assertIn("wurde wieder geöffnet", unquote(self._reopen(ticket_id).headers["location"]))
        self.assertEqual(pulse_db.get_ticket(ticket_id=ticket_id)["status"], "open")

    def test_reopened_ticket_gets_a_fresh_waiting_period(self):
        import json
        ticket_id, _owner, _channel = self._prepare_closed_ticket_reopen()
        with pulse_db.connect() as db:
            db.execute(
                "UPDATE tickets SET opened_at='2026-01-01T00:00:00+00:00', automation_state=? WHERE id=?",
                (json.dumps({"unclaimed_ping": "2026-01-01T01:00:00+00:00"}), ticket_id),
            )

        self._reopen(ticket_id)

        ticket = pulse_db.get_ticket(ticket_id=ticket_id)
        state = json.loads(ticket["automation_state"])
        self.assertEqual(state["reopen_count"], 1)
        self.assertNotIn("unclaimed_ping", state)
        from datetime import datetime, timezone
        from pulse_ticket_design import automation_settings
        from pulse_ticket_triage import triage
        info = triage(ticket, datetime.now(timezone.utc), automation_settings({"unclaimed_ping_minutes": 30}))
        self.assertEqual(info["state"], "new")
        self.assertFalse(info["overdue"])

    def test_escalation_and_reopen_window_are_saved_and_clamped_by_the_admin_route(self):
        response = self.client.post(
            "/tickets/automation",
            data={
                "unclaimed_ping_minutes": "30",
                "staff_reply_hours": "12",
                "user_reminder_hours": "24",
                "auto_close_hours": "0",
                "escalation_minutes": "99999",
                "reopen_window_days": "9999",
                "sla__support__escalation_minutes": "15",
                "sla__bug__escalation_minutes": "0",
                "sla__support__auto_close_hours": "1",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        saved = pulse_db.get_setting("ticket_system_config", {})
        self.assertEqual(saved["automation"]["escalation_minutes"], 1440)
        self.assertEqual(saved["automation"]["category_sla"],
                         {"support": {"escalation_minutes": 15}, "bug": {"escalation_minutes": 0}})
        self.assertEqual(saved["reopen"], {"window_days": 365})

    def test_older_forms_without_the_new_fields_keep_both_features_off(self):
        response = self.client.post(
            "/tickets/automation",
            data={"unclaimed_ping_minutes": "30", "staff_reply_hours": "12",
                  "user_reminder_hours": "24", "auto_close_hours": "0"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        saved = pulse_db.get_setting("ticket_system_config", {})
        self.assertEqual(saved["automation"]["escalation_minutes"], 0)
        self.assertEqual(saved["reopen"], {"window_days": 0})

    def test_escalation_settings_require_admin(self):
        self.perms["is_admin"] = False
        response = self.client.post(
            "/tickets/automation",
            data={"escalation_minutes": "5", "reopen_window_days": "3"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(pulse_db.get_setting("ticket_system_config", {}), {})

    def test_category_sla_settings_are_saved_by_the_admin_route(self):
        response = self.client.post(
            "/tickets/automation",
            data={
                "unclaimed_ping_minutes": "30",
                "staff_reply_hours": "12",
                "user_reminder_hours": "24",
                "auto_close_hours": "0",
                "sla__support__staff_reply_hours": "4",
                "sla__bug__unclaimed_ping_minutes": "0",
                "sla__../unsafe__staff_reply_hours": "1",
                "auto_assign_enabled": "true",
                "max_open_per_staff": "3",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        saved_config = pulse_db.get_setting("ticket_system_config", {})
        saved = saved_config["automation"]
        self.assertEqual(
            saved["category_sla"],
            {
                "support": {"staff_reply_hours": 4},
                "bug": {"unclaimed_ping_minutes": 0},
            },
        )
        self.assertEqual(saved["staff_reply_hours"], 12)
        self.assertEqual(saved_config["distribution"], {"enabled": True, "max_open_per_staff": 3})

    def test_category_sla_settings_require_admin_and_blank_fields_remove_overrides(self):
        pulse_db.set_setting("ticket_system_config", {
            "automation": {
                "staff_reply_hours": 12,
                "category_sla": {"support": {"staff_reply_hours": 4}},
            },
        })
        self.perms["is_admin"] = False
        denied = self.client.post(
            "/tickets/automation",
            data={
                "unclaimed_ping_minutes": "30",
                "staff_reply_hours": "12",
                "user_reminder_hours": "24",
                "auto_close_hours": "0",
                "sla__support__staff_reply_hours": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(
            pulse_db.get_setting("ticket_system_config", {})["automation"]["category_sla"],
            {"support": {"staff_reply_hours": 4}},
        )

        self.perms["is_admin"] = True
        saved_response = self.client.post(
            "/tickets/automation",
            data={
                "unclaimed_ping_minutes": "30",
                "staff_reply_hours": "12",
                "user_reminder_hours": "24",
                "auto_close_hours": "0",
                "sla__support__staff_reply_hours": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(saved_response.status_code, 303)
        self.assertEqual(
            pulse_db.get_setting("ticket_system_config", {})["automation"]["category_sla"],
            {},
        )

