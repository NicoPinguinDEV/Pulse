"""BOT-09 (E-8): citizen moderation – rights (Pulse right AND Discord permission), hierarchy, case file,
DM before acting, expiry of time-limited bans, dashboard case file."""
import asyncio
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from fastapi.testclient import TestClient

import pulse_db
import pulse_mod_cases as mc
import webserver as ws
from tests.test_pulse_admin_area import ADMIN, TEAMLER, fake_auth_for


class Role:
    def __init__(self, rid, position):
        self.id, self.position = rid, position

    def __ge__(self, other):
        return self.position >= other.position


def member(uid, position, roles=(), bot=False):
    return SimpleNamespace(id=uid, bot=bot, display_name=f"M{uid}", top_role=Role(900 + uid, position), roles=list(roles),
                           send=AsyncMock(), timeout=AsyncMock(), kick=AsyncMock())


class TempDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self.tmp.name)
        self.patches = [patch.object(pulse_db, "DB_PATH", str(root / "pulse.db")),
                        patch.object(ws, "AUDIT_FILE", str(root / "audit_logs.json")),
                        patch("pulse_panels.BASE_DIR", root)]
        for p in self.patches:
            p.start()
        pulse_db.init_db()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()


class RuleTests(TempDb):
    def test_needs_pulse_right_and_discord_permission(self):
        kick_perm = SimpleNamespace(kick_members=True, moderate_members=False, ban_members=False, administrator=False)
        self.assertTrue(mc.may_moderate("kick", {"can_moderate_kicks": True}, kick_perm))
        self.assertFalse(mc.may_moderate("kick", {}, kick_perm))                               # Discord right alone is not enough
        self.assertFalse(mc.may_moderate("kick", {"can_moderate_kicks": True}, SimpleNamespace()))  # Pulse right alone is not enough
        self.assertFalse(mc.may_moderate("ban", {"can_moderate_kicks": True}, kick_perm))
        self.assertTrue(mc.may_moderate("ban", {"is_admin": True}, SimpleNamespace(administrator=True)))
        self.assertFalse(mc.may_moderate("bogus", {"is_admin": True}, SimpleNamespace(administrator=True)))

    def test_target_rules(self):
        guild = SimpleNamespace(owner_id=1, me=member(99, 50))
        actor = member(2, 20)
        self.assertEqual(mc.target_problem(actor, member(3, 10), guild, []), "")
        self.assertIn("Rollenhierarchie", mc.target_problem(actor, member(3, 30), guild, []))
        self.assertIn("Teammitglieder", mc.target_problem(actor, member(3, 10, roles=[Role(7, 1)]), guild, [7]))
        self.assertIn("selbst", mc.target_problem(actor, actor, guild, []))
        self.assertIn("Serverinhaber", mc.target_problem(actor, member(1, 1), guild, []))
        self.assertIn("Pulse steht", mc.target_problem(member(1, 99), member(3, 60), guild, []))

    def test_case_file_and_expiry(self):
        with self.assertRaises(ValueError):
            mc.create_case(1, member(3, 1), "warn", "kurz", "", None, member(2, 5))
        with self.assertRaises(ValueError):
            mc.create_case(1, member(3, 1), "timeout", "Beleidigung im Chat", "", None, member(2, 5))
        case = mc.create_case(1, member(3, 1), "ban", "Wiederholter Spam", "https://beweis.example", 60, member(2, 5))
        self.assertTrue(case["id"].startswith("MOD-"))
        self.assertEqual(mc.get_case(case["id"])["reason"], "Wiederholter Spam")
        self.assertEqual(mc.expired_bans(), [])
        self.assertEqual(len(mc.expired_bans(datetime.now(timezone.utc) + timedelta(hours=2))), 1)
        text = mc.dm_text(case, "Bochum RP", mc.appeal_text(1, case["id"]))
        self.assertIn(case["id"], text)
        self.assertIn("Einspruch", text)
        self.assertIn("Button", text)

    def test_appeal_is_owner_scoped_unique_and_human_decided_without_changing_sanction(self):
        case = mc.create_case(1, member(300, 1), "ban", "Wiederholter Spam", "", None, member(200, 5))
        self.assertIn(case["id"], [row["id"] for row in mc.list_appealable_cases()])
        appeal = mc.submit_appeal(
            1, 300, case["id"],
            "Ich möchte die Entscheidung prüfen lassen, weil wichtige Informationen fehlen.",
        )
        self.assertNotIn(case["id"], [row["id"] for row in mc.list_appealable_cases()])
        self.assertEqual(appeal["status"], "pending")
        self.assertEqual(mc.list_appeals(1), [appeal])
        self.assertEqual(mc.list_appeals(2), [])
        self.assertEqual(mc.list_appeals(1, status="accepted"), [])
        with self.assertRaisesRegex(ValueError, "gehört nicht"):
            mc.submit_appeal(
                1, 301, case["id"],
                "Ich möchte die Entscheidung prüfen lassen, weil wichtige Informationen fehlen.",
            )
        with self.assertRaisesRegex(ValueError, "bereits ein Einspruch"):
            mc.submit_appeal(
                1, 300, case["id"],
                "Ich möchte die Entscheidung prüfen lassen, weil wichtige Informationen fehlen.",
            )

        decided = mc.decide_appeal(
            1, case["id"], "accepted", "Zusätzliche Belege wurden geprüft.",
            201, "Leitung",
        )
        self.assertEqual((decided["status"], decided["reviewed_by_id"]), ("accepted", "201"))
        # An appeal decision is a separate record; the original ban is not lifted or rewritten.
        self.assertEqual(mc.get_case(case["id"])["status"], "active")
        self.assertEqual(mc.get_case(case["id"])["action"], "ban")
        self.assertEqual(mc.list_appeals(1, status=None)[0]["decision_note"], "Zusätzliche Belege wurden geprüft.")
        with self.assertRaisesRegex(ValueError, "bereits entschieden"):
            mc.decide_appeal(1, case["id"], "rejected", "Weitere Prüfung ohne neue Fakten.", 202, "Admin")

    def test_appeal_deadline_and_reason_length_are_enforced(self):
        created = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)
        with patch.object(mc, "_now", lambda: created):
            case = mc.create_case(1, member(310, 1), "warn", "Spam im Chat", "", None, member(210, 5))
        with self.assertRaisesRegex(ValueError, "14 Tagen"):
            mc.submit_appeal(
                1, 310, case["id"],
                "Ich möchte die Entscheidung prüfen lassen, weil wichtige Informationen fehlen.",
                now=created + timedelta(days=15),
            )
        with self.assertRaisesRegex(ValueError, "20 bis 1500"):
            mc.submit_appeal(1, 310, case["id"], "Zu kurz", now=created + timedelta(days=1))


class CogTests(TempDb):
    def interaction(self, actor):
        sent = []
        return SimpleNamespace(guild=SimpleNamespace(id=1, name="G", owner_id=1, me=member(99, 50), ban=AsyncMock()),
                               user=actor, followup=SimpleNamespace(send=AsyncMock(side_effect=lambda t, **k: sent.append(t)))), sent

    def test_dm_goes_out_before_the_kick_and_case_is_logged(self):
        from cogs.mod_cases import ModCases
        actor, target = member(2, 20), member(3, 10)
        order = []
        target.send = AsyncMock(side_effect=lambda *a, **k: order.append("dm"))
        target.kick = AsyncMock(side_effect=lambda *a, **k: order.append("kick"))
        interaction, sent = self.interaction(actor)
        cog = ModCases.__new__(ModCases)
        replies = []

        async def respond(text):
            replies.append(text)
        asyncio.run(cog._execute(interaction, "kick", target, "Werbung für anderen Server", "", None, respond))
        self.assertEqual(order, ["dm", "kick"])
        self.assertIn("Fall `MOD-", replies[0])
        sent_kwargs = target.send.await_args.kwargs
        self.assertIsNotNone(sent_kwargs.get("view"))
        self.assertEqual(sent_kwargs["view"].children[0].custom_id, f"pulse_mod:appeal:{mc.list_cases(3)[0]['id']}")
        stored = mc.list_cases(3)[0]
        self.assertEqual((stored["action"], stored["dm_sent"], stored["status"]), ("kick", 1, "active"))

    def test_discord_refusal_marks_the_case_failed(self):
        from cogs.mod_cases import ModCases
        target = member(3, 10)
        target.timeout = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no"))
        interaction, _ = self.interaction(member(2, 20))
        replies = []

        async def respond(text):
            replies.append(text)
        asyncio.run(ModCases.__new__(ModCases)._execute(interaction, "timeout", target, "Spam im Chat", "", 30, respond))
        self.assertIn("verweigert", replies[0])
        self.assertEqual(mc.list_cases(3)[0]["status"], "failed")

    def test_appeal_button_only_opens_for_the_affected_user(self):
        from cogs.mod_cases import AppealView

        bot = SimpleNamespace()
        view = AppealView("MOD-123ABC", 1, 300, bot)
        button = view.children[0]
        self.assertEqual(button.custom_id, "pulse_mod:appeal:MOD-123ABC")

        other_response = SimpleNamespace(send_message=AsyncMock(), send_modal=AsyncMock())
        other = SimpleNamespace(user=SimpleNamespace(id=301), response=other_response)
        asyncio.run(button.callback(other))
        other_response.send_message.assert_awaited_once()
        self.assertTrue(other_response.send_message.await_args.kwargs["ephemeral"])
        other_response.send_modal.assert_not_awaited()

        own_response = SimpleNamespace(send_message=AsyncMock(), send_modal=AsyncMock())
        own = SimpleNamespace(user=SimpleNamespace(id=300), response=own_response)
        with patch.object(mc, "can_submit_appeal", return_value={"case": {}, "deadline": datetime.now(timezone.utc)}):
            asyncio.run(button.callback(own))
        own_response.send_modal.assert_awaited_once()
        self.assertTrue(own_response.send_modal.await_args.args[0].title.startswith("Moderationsentscheidung"))

    def test_expired_ban_is_lifted_once(self):
        from cogs.mod_cases import lift_expired_bans
        case = mc.create_case(1, member(3, 1), "ban", "Wiederholter Spam", "", 1, member(2, 5))
        guild = SimpleNamespace(unban=AsyncMock(), fetch_ban=AsyncMock(return_value=SimpleNamespace(reason=f"{case['id']} · Mod: Spam")))
        bot = SimpleNamespace(get_guild=lambda gid: guild)
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        with patch.object(mc, "_now", lambda: future):
            self.assertEqual(asyncio.run(lift_expired_bans(bot)), 1)
            self.assertEqual(asyncio.run(lift_expired_bans(bot)), 0)
        self.assertEqual(mc.get_case(case["id"])["status"], "expired")

    def test_newer_ban_by_hand_is_not_lifted(self):
        # review finding: an expired Pulse case lifted a later permanent ban set by hand
        from cogs.mod_cases import lift_expired_bans
        case = mc.create_case(1, member(3, 1), "ban", "Wiederholter Spam", "", 1, member(2, 5))
        guild = SimpleNamespace(unban=AsyncMock(), fetch_ban=AsyncMock(return_value=SimpleNamespace(reason="Dauerhaft: Betrug")))
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        with patch.object(mc, "_now", lambda: future):
            self.assertEqual(asyncio.run(lift_expired_bans(SimpleNamespace(get_guild=lambda gid: guild))), 0)
        guild.unban.assert_not_awaited()
        self.assertEqual(mc.get_case(case["id"])["status"], "expired")

    def test_forbidden_unban_is_marked_once(self):
        from cogs.mod_cases import lift_expired_bans
        case = mc.create_case(1, member(3, 1), "ban", "Wiederholter Spam", "", 1, member(2, 5))
        guild = SimpleNamespace(fetch_ban=AsyncMock(return_value=SimpleNamespace(reason=case["id"])),
                                unban=AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no")))
        future = datetime.now(timezone.utc) + timedelta(minutes=5)
        with patch.object(mc, "_now", lambda: future):
            asyncio.run(lift_expired_bans(SimpleNamespace(get_guild=lambda gid: guild)))
            asyncio.run(lift_expired_bans(SimpleNamespace(get_guild=lambda gid: guild)))
        self.assertEqual(guild.unban.await_count, 1)                    # no retry every minute
        self.assertEqual(mc.get_case(case["id"])["status"], "failed")

    def test_refused_timeout_sends_no_dm(self):
        from cogs.mod_cases import ModCases
        target = member(3, 10)
        target.timeout = AsyncMock(side_effect=discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no"))
        interaction, _ = self.interaction(member(2, 20))

        async def respond(text):
            pass
        asyncio.run(ModCases.__new__(ModCases)._execute(interaction, "timeout", target, "Spam im Chat", "", 30, respond))
        target.send.assert_not_awaited()

    def test_no_automatic_trigger(self):
        source = (Path(mc.__file__).parent / "cogs" / "mod_cases.py").read_text(encoding="utf-8")
        self.assertNotIn("Cog.listener", source)  # actions only from commands by people


class PageTests(TempDb):
    def test_case_file_rights_and_search(self):
        mc.create_case(1, member(3, 1), "warn", "Beleidigung im Voice", "", None, member(2, 5))
        mc.create_case(1, member(4, 1), "warn", "Spam im Chat", "", None, member(2, 5))
        client = TestClient(ws.app, raise_server_exceptions=False)
        with patch.object(ws, "auth", fake_auth_for(TEAMLER)):
            self.assertEqual(client.get("/mod-cases").status_code, 403)
        case = mc.create_case(1, member(3, 1), "warn", "Beleidigung im Voice", "", None, member(2, 5))
        mc.submit_appeal(
            1, 3, case["id"],
            "Ich möchte diese Entscheidung prüfen lassen, weil Kontext im Gespräch fehlt.",
        )
        with patch.object(ws, "auth", fake_auth_for(ADMIN)):
            page = client.get("/mod-cases", params={"q": "voice"})
            self.assertEqual(page.status_code, 200)
            self.assertIn("Beleidigung im Voice", page.text)
            self.assertNotIn("Spam im Chat", page.text)
            self.assertIn("Offene Einsprüche", page.text)
            self.assertIn(case["id"], page.text)
            csv = client.get("/mod-cases.csv")
            self.assertIn("Spam im Chat", csv.content.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
