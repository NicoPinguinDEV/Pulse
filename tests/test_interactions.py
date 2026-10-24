"""BOT-01: shared button/select rules and the bot-wide mention default."""
import ast
import asyncio
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

import pulse_interactions as pi

ROOT = Path(pi.__file__).parent


def interaction(user_id=1, custom_id="btn", done=False):
    response = SimpleNamespace(is_done=lambda: done, send_message=AsyncMock(), defer=AsyncMock())
    return SimpleNamespace(user=SimpleNamespace(id=user_id), data={"custom_id": custom_id}, response=response,
                           followup=SimpleNamespace(send=AsyncMock()))


class ViewRuleTests(unittest.TestCase):
    def setUp(self):
        pi._last_click.clear()

    def test_double_click_is_stopped_per_person_and_button(self):
        self.assertFalse(pi.too_fast(1, "a", now=10.0))
        self.assertTrue(pi.too_fast(1, "a", now=10.5))
        self.assertFalse(pi.too_fast(2, "a", now=10.6))   # someone else
        self.assertFalse(pi.too_fast(1, "b", now=10.7))   # another button
        self.assertFalse(pi.too_fast(1, "a", now=13.0))   # after the cooldown

    def test_click_map_stays_bounded(self):
        for n in range(pi._MAX_TRACKED + 10):
            pi.too_fast(n, "x", now=float(n))
        self.assertLessEqual(len(pi._last_click), pi._MAX_TRACKED)

    def test_error_answers_with_an_error_id(self):
        async def go():
            view = pi.PulseView(timeout=None)
            i = interaction()
            await view.on_error(i, RuntimeError("kaputt"), SimpleNamespace(custom_id="x"))
            return i.response.send_message.await_args
        args = asyncio.run(go())
        self.assertIn("Fehler-ID", args.args[0])
        self.assertTrue(args.kwargs["ephemeral"])

    def test_error_after_defer_uses_followup(self):
        async def go():
            i = interaction(done=True)
            await pi.PulseView(timeout=None).on_error(i, discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no"), None)
            return i.followup.send.await_args
        self.assertIn("Rechte/Rollenreihenfolge", asyncio.run(go()).args[0])

    def test_second_fast_click_is_refused(self):
        async def go():
            view = pi.PulseView(timeout=None)
            first = await view.interaction_check(interaction())
            i = interaction()
            second = await view.interaction_check(i)
            return first, second, i.response.send_message.await_args
        first, second, reply = asyncio.run(go())
        self.assertEqual((first, second), (True, False))
        self.assertIn("nicht doppelt", reply.args[0])

    def test_timeout_disables_components(self):
        async def go():
            view = pi.PulseView(timeout=1)
            view.add_item(discord.ui.Button(label="x", custom_id="x"))
            view.message = SimpleNamespace(edit=AsyncMock())
            await view.on_timeout()
            return view
        view = asyncio.run(go())
        self.assertTrue(all(c.disabled for c in view.children))
        view.message.edit.assert_awaited()

    def test_message_delete_still_needs_confirmation(self):
        from cogs.messagedelete import MessageDeleteConfirmationView
        self.assertTrue(issubclass(MessageDeleteConfirmationView, discord.ui.View))
        self.assertTrue(issubclass(MessageDeleteConfirmationView, pi.PulseView))
        # its own interaction_check (only the requester may confirm) still wins over the shared one
        self.assertIsNot(MessageDeleteConfirmationView.interaction_check, pi.PulseView.interaction_check)

    def test_all_views_use_the_shared_base(self):
        plain = [f"{p.name}:{m.group(0)}" for p in ROOT.glob("cogs/*.py")
                 for m in re.finditer(r"^class \w+\(discord\.ui\.View\):", p.read_text(encoding="utf-8"), re.M)]
        self.assertEqual(plain, [])


class MentionTests(unittest.TestCase):
    def test_default_blocks_everyone_and_roles_but_not_people(self):
        d = pi.DEFAULT_ALLOWED_MENTIONS
        self.assertEqual((d.everyone, d.roles, d.users), (False, False, True))
        self.assertIn("allowed_mentions=DEFAULT_ALLOWED_MENTIONS", (ROOT / "main.py").read_text(encoding="utf-8"))

    def test_wanted_pings_allow_themselves_explicitly(self):
        # inventory from the plan: recruiting, verification reminder, ticket ping roles, office roles,
        # RP @everyone, social promo – each passes its own allowed_mentions
        checks = {
            "webserver.py": "content = alert_role.mention if alert_role else",
            "cogs/verification.py": "reminder_content(settings, role_id=UNVERIFIED_ROLE_ID",
            "cogs/rp_verwaltung.py": "allowed_mentions=discord.AllowedMentions(everyone=True, users=False, roles=False)",
            "cogs/buero_system.py": "role_mentions = \" \".join(role.mention for role in office_roles)",
            "cogs/social_promo.py": "content = f\"<@&{SOCIAL_PING_ROLE_ID}>\\n\"",
        }
        for rel, marker in checks.items():
            src = (ROOT / rel).read_text(encoding="utf-8")
            pos = src.index(marker)
            self.assertIn("allowed_mentions", src[pos:pos + 3000], rel)

    def test_no_send_with_literal_role_or_everyone_ping_lacks_allowed_mentions(self):
        offenders = []
        files = [ROOT / "webserver.py", ROOT / "main.py", *ROOT.glob("pulse_*.py"), *ROOT.glob("cogs/*.py")]
        for f in files:
            src = f.read_text(encoding="utf-8")
            for node in ast.walk(ast.parse(src)):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                if name not in {"send", "send_message", "edit_message", "reply"}:
                    continue
                if any(k.arg == "allowed_mentions" for k in node.keywords):
                    continue
                text = " ".join(ast.get_source_segment(src, a) or "" for a in node.args)
                text += " ".join(ast.get_source_segment(src, k.value) or "" for k in node.keywords if k.arg == "content")
                if "<@&" in text or "@everyone" in text or "@here" in text:
                    offenders.append(f"{f.name}:{node.lineno}")
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
