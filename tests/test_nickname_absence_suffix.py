"""Prefix sync and absence suffix must not rename each other (live: same members every restart)."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from cogs import role_nickname_prefixes as cog_module
from pulse_nickname_utils import format_prefixed_nickname, split_absence_suffix


class FakeRole:
    def __init__(self, rid, name, position):
        self.id, self.name, self.position = rid, name, position

    def is_default(self):
        return False


class FakeMember:
    def __init__(self, nick, roles):
        self.id = 853610960635428865
        self.name = "blue_whale_238"
        self.nick = nick
        self.bot = False
        self.roles = roles
        self.guild = SimpleNamespace(id=1)
        self.edits = []

    async def edit(self, nick=None, reason=None):
        self.edits.append(nick)
        self.nick = nick

    def __str__(self):
        return self.name


def run(member, state, *, bot=None, nickname_changed=False):
    cog = cog_module.RoleNicknamePrefixes(bot=bot)
    cog._member_state_cache = {str(member.id): state} if state else {}
    cog._bulk_state_batching = True  # never touch the real state file in tests
    with patch.object(cog_module, "GUILD_ID", 0), patch("builtins.print"):
        result = asyncio.run(cog._synchronize_member_locked(
            member, config_snapshot={}, nickname_changed=nickname_changed
        ))
    return result, cog._member_state_cache.get(str(member.id))


MANAGEMENT = [FakeRole(1, "》BORP メ Management", 50)]
STATE = {"original_nick": "Richy", "applied_prefix": "MA ✗", "role_id": "1", "last_applied_nick": "MA ✗ Richy"}


class HelperTests(unittest.TestCase):
    def test_split_and_format_with_suffix(self):
        self.assertEqual(split_absence_suffix("MA ✗ Richy | Abgemeldet"), ("MA ✗ Richy", " | Abgemeldet"))
        self.assertEqual(split_absence_suffix("MA ✗ Richy"), ("MA ✗ Richy", ""))
        self.assertEqual(split_absence_suffix(None), (None, ""))
        self.assertEqual(format_prefixed_nickname("MA ✗", "Richy", " | Abgemeldet"), "MA ✗ Richy | Abgemeldet")
        long = format_prefixed_nickname("Stv.MA ✗", "MrJoela/BabyFace", " | Abgemeldet")
        self.assertLessEqual(len(long), 32)
        self.assertTrue(long.endswith(" | Abgemeldet"))


class SyncTests(unittest.TestCase):
    def test_absent_member_keeps_suffix_and_is_not_renamed(self):
        member = FakeMember("MA ✗ Richy | Abgemeldet", MANAGEMENT)
        result, state = run(member, dict(STATE))
        self.assertEqual(result, "unchanged")
        self.assertEqual(member.edits, [])
        self.assertEqual(state["original_nick"], "Richy")  # suffix never stored as part of the name

    def test_prefix_is_fixed_but_suffix_kept(self):
        member = FakeMember("Mod ✗ Richy | Abgemeldet", MANAGEMENT)  # old rank prefix after a promotion
        result, _ = run(member, {"original_nick": "Richy", "applied_prefix": "Mod ✗", "role_id": "9"})
        self.assertEqual(result, "updated")
        self.assertEqual(member.edits, ["MA ✗ Richy | Abgemeldet"])

    def test_after_absence_ends_the_normal_name_returns(self):
        member = FakeMember("MA ✗ Richy", MANAGEMENT)
        result, _ = run(member, dict(STATE))
        self.assertEqual(result, "unchanged")
        self.assertEqual(member.edits, [])

    def test_manual_name_change_is_kept_even_when_prefix_stays_the_same(self):
        member = FakeMember("MA ✗ Richy 2.0", MANAGEMENT)
        result, updated_state = run(member, dict(STATE), nickname_changed=True)
        self.assertEqual(result, "unchanged")
        self.assertEqual(member.edits, [])
        self.assertEqual(updated_state["original_nick"], "Richy 2.0")
        self.assertEqual(updated_state["last_applied_nick"], "MA ✗ Richy 2.0")

    def test_absence_suffix_requires_real_bot_absence_record(self):
        member = FakeMember("MA ✗ Richy | Abgemeldet", MANAGEMENT)
        absence_cog = SimpleNamespace(is_abgemeldet=lambda user_id: False)
        bot = SimpleNamespace(get_cog=lambda name: absence_cog if name == "AbmeldungCog" else None)
        result, _ = run(member, dict(STATE), bot=bot)
        self.assertEqual(result, "updated")
        self.assertEqual(member.edits, ["MA ✗ Richy"])

    def test_absent_member_name_edit_keeps_authoritative_suffix(self):
        member = FakeMember("MA ✗ New Richy", MANAGEMENT)
        absence_cog = SimpleNamespace(is_abgemeldet=lambda user_id: True)
        bot = SimpleNamespace(get_cog=lambda name: absence_cog if name == "AbmeldungCog" else None)
        result, state = run(member, dict(STATE), bot=bot, nickname_changed=True)
        self.assertEqual(result, "updated")
        self.assertEqual(member.edits, ["MA ✗ New Richy | Abgemeldet"])
        self.assertEqual(state["original_nick"], "New Richy")

    def test_no_ping_pong_over_several_cycles(self):
        member = FakeMember("MA ✗ Richy", MANAGEMENT)
        state = dict(STATE)
        for _ in range(3):
            # absence cog adds the suffix (as cogs/abmeldung.apply_user_status does) …
            if " | Abgemeldet" not in member.nick:
                member.nick = member.nick + " | Abgemeldet"
            # … and the prefix sync reacts to the nickname change
            _, state = run(member, state)
        self.assertEqual(member.edits, [])
        self.assertEqual(member.nick, "MA ✗ Richy | Abgemeldet")


if __name__ == "__main__":
    unittest.main()
