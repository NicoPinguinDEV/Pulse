"""Anti-Nuke: mass role assignment (fixed threshold 10 new roles / 10 s per actor, full role quarantine).

Only local fakes - no Discord connection, temp database.
"""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

import pulse_db
from cogs import antinuke
from tests import test_antinuke_control_center as base
from tests.fakes import FakeMember, FakeRole

ATTACKER, RECIPIENT, HELPER = 30, 900, 33
MEMBER_ROLE_UPDATE = discord.AuditLogAction.member_role_update


class Everyone(FakeRole):
    def is_default(self) -> bool:
        return True


def roles(n, start=100, **kw):
    return [FakeRole(start + i, f"R{start + i}", position=2, **kw) for i in range(n)]


def grant_entry(entry_id, actor, target=RECIPIENT, added=None):
    item = base.entry(entry_id, actor, MEMBER_ROLE_UPDATE, target=target)
    if added is not None:
        item.after = SimpleNamespace(roles=added)
        item.before = SimpleNamespace(roles=[])
    return item


class RoleGrantTests(unittest.TestCase):
    setUp = base.RuleEngineTests.setUp
    tearDown = base.RuleEngineTests.tearDown
    cog = base.RuleEngineTests.cog
    config = base.RuleEngineTests.config

    def setup_guild(self, mode="protect", extra_rules=(), actor_roles=None, **cfg):
        everyone = Everyone(7, "@everyone", position=0)
        self.attacker = FakeMember(ATTACKER, "Angreifer", roles=actor_roles if actor_roles is not None else [everyone, *roles(3, 200)])
        self.recipient = FakeMember(RECIPIENT, "Empfaenger", roles=[everyone, *roles(2, 300)])
        guild = base.FakeGuild(members=[self.attacker, self.recipient])
        guild.fetch_member = AsyncMock(return_value=None)
        cog = self.cog(self.config(*extra_rules, mode=mode, **cfg))
        return cog, guild

    def grant(self, cog, guild, entry_id, n=1, actor=ATTACKER, added=True, step=0.0):
        """One member update that adds n roles; the audit log carries a matching entry."""
        new = roles(n, start=1000 + entry_id * 20)
        guild.entries.append(grant_entry(entry_id, actor, added=new if added else None))
        before = SimpleNamespace(roles=[], guild=guild, id=RECIPIENT)
        after = SimpleNamespace(roles=new, guild=guild, id=RECIPIENT)
        asyncio.run(cog.on_member_update(before, after))
        self.clock.now += step

    def events(self, title):
        return pulse_db.events(event_type=title)

    def test_one_to_eight_grants_do_nothing(self):
        cog, guild = self.setup_guild()
        for i in range(1, 9):
            self.grant(cog, guild, i, step=0.5)
        self.assertEqual(self.attacker.removed_roles, [])
        self.assertEqual(self.events("Anti-Nuke: Rollenvergabe-Warnung"), [])
        cog._alert.assert_not_awaited()

    def test_ninth_warns_without_sanction_tenth_quarantines(self):
        cog, guild = self.setup_guild()
        for i in range(1, 9):
            self.grant(cog, guild, i, step=0.5)
        self.grant(cog, guild, 9, step=0.5)
        self.assertEqual(self.attacker.removed_roles, [])
        self.assertEqual(len(self.events("Anti-Nuke: Rollenvergabe-Warnung")), 1)
        self.grant(cog, guild, 10)
        self.assertEqual(len(self.attacker.removed_roles), 3)
        self.assertEqual(len(self.events("Anti-Nuke: Massen-Rollenvergabe")), 1)

    def test_several_roles_in_one_update_are_counted_by_their_number(self):
        cog, guild = self.setup_guild()
        self.grant(cog, guild, 1, n=9)
        self.assertEqual(self.attacker.removed_roles, [])
        self.assertEqual(len(self.events("Anti-Nuke: Rollenvergabe-Warnung")), 1)
        self.grant(cog, guild, 2, n=1)
        self.assertEqual(len(self.attacker.removed_roles), 3)

    def test_ten_roles_in_a_single_update_trigger_at_once(self):
        cog, guild = self.setup_guild()
        self.grant(cog, guild, 1, n=10)
        self.assertEqual(len(self.attacker.removed_roles), 3)

    def test_different_actors_are_not_added_together(self):
        cog, guild = self.setup_guild()
        other = FakeMember(HELPER, "Helfer", roles=roles(2, 400))
        guild.members.append(other)
        for i in range(1, 6):
            self.grant(cog, guild, i, actor=ATTACKER)
            self.grant(cog, guild, 100 + i, actor=HELPER)
        self.assertEqual(self.attacker.removed_roles, [])
        self.assertEqual(other.removed_roles, [])

    def test_window_expiry_resets_the_count(self):
        cog, guild = self.setup_guild()
        for i in range(1, 6):
            self.grant(cog, guild, i)
        self.clock.now += 11
        for i in range(6, 11):
            self.grant(cog, guild, i)
        self.assertEqual(self.attacker.removed_roles, [])

    def test_removals_and_other_events_do_not_count(self):
        cog, guild = self.setup_guild()
        for i in range(1, 12):  # role removals: after.roles is empty -> not a grant
            guild.entries.append(grant_entry(i, ATTACKER, added=[]))
            before = SimpleNamespace(roles=roles(1, 500 + i), guild=guild, id=RECIPIENT)
            asyncio.run(cog.on_member_update(before, SimpleNamespace(roles=[], guild=guild, id=RECIPIENT)))
        for i in range(20, 32):  # channel/role-update events never feed the grant counter
            guild.entries.append(base.entry(i, ATTACKER, base.CHANNEL_DELETE))
            asyncio.run(cog._handle_event(guild, "channel_delete", None))
        role_edit = SimpleNamespace(permissions=discord.Permissions(), position=1, name="x", color=1)
        asyncio.run(cog.on_guild_role_update(SimpleNamespace(permissions=role_edit.permissions, position=1, guild=guild),
                                              SimpleNamespace(permissions=role_edit.permissions, position=2, guild=guild, id=1)))
        self.assertEqual(self.attacker.removed_roles, [])
        self.assertEqual(self.events("Anti-Nuke: Massen-Rollenvergabe"), [])

    def test_global_and_individual_rules_cannot_lower_or_bypass_the_threshold(self):
        rule = base.rule("rechte", ["member_role_grant"], 1, 10, ["log", "alert"])
        cog, guild = self.setup_guild(extra_rules=(rule,), action_limit=3, window_seconds=10)
        for i in range(1, 4):
            self.grant(cog, guild, i)
        self.assertEqual(self.attacker.removed_roles, [], "weder Regel mit Schwelle 1 noch globale 3 löst die Rollen-Quarantäne aus")
        for i in range(4, 11):
            self.grant(cog, guild, i)
        self.assertEqual(len(self.attacker.removed_roles), 3)

    def test_trusted_owner_and_bot_are_protected(self):
        cog, guild = self.setup_guild(trusted_user_ids=[ATTACKER])
        self.grant(cog, guild, 1, n=12)
        self.assertEqual(self.attacker.removed_roles, [])
        cog, guild = self.setup_guild()
        for actor in (base.OWNER, base.BOT):
            self.grant(cog, guild, actor + 500, n=12, actor=actor)
        self.assertEqual(self.attacker.removed_roles, [])

    def test_trusted_role_is_protected(self):
        trusted = FakeRole(777, "Vertraut", position=2)
        cog, guild = self.setup_guild(actor_roles=[trusted, *roles(2, 200)], trusted_role_ids=[777])
        self.grant(cog, guild, 1, n=12)
        self.assertEqual(self.attacker.removed_roles, [])

    def test_recipient_keeps_roles_and_actor_is_the_target(self):
        cog, guild = self.setup_guild()
        before = list(self.recipient.roles)
        self.grant(cog, guild, 1, n=10)
        self.assertEqual(self.recipient.roles, before)
        self.assertEqual(self.recipient.removed_roles, [])
        self.assertEqual(len(self.attacker.removed_roles), 3)

    def test_recipient_never_counts_as_actor(self):
        cog, guild = self.setup_guild()
        self.grant(cog, guild, 1, n=10, actor=ATTACKER)
        self.assertNotIn((guild.id, RECIPIENT), cog._role_grants)

    def test_full_quarantine_removes_every_removable_role_and_is_restorable(self):
        everyone, managed, high = Everyone(7, "@everyone", 0), FakeRole(8, "Bot", 2, managed=True), FakeRole(9, "Hoch", 10)
        normal = roles(3, 200)
        cog, guild = self.setup_guild(actor_roles=[everyone, managed, high, *normal])
        self.grant(cog, guild, 1, n=10)
        self.assertEqual({r.id for r in self.attacker.removed_roles}, {r.id for r in normal})
        stored = antinuke.load_quarantine()[str(ATTACKER)]
        self.assertEqual(stored["kind"], "full_roles")
        self.assertEqual(set(stored["role_ids"]), {r.id for r in normal})
        failed = {f["id"] for f in stored["failed_roles"]}
        self.assertEqual(failed, {8, 9}, "verwaltete Rolle und Rolle über der Bot-Rolle werden protokolliert, nicht angefasst")
        self.assertIn("TEILWEISE", self.events("Anti-Nuke: Massen-Rollenvergabe")[0]["payload_json"])
        # restore through the existing dashboard function
        guild.get_role = lambda rid: next((r for r in normal if r.id == int(rid)), None)
        result = asyncio.run(antinuke.restore_quarantine(guild, ATTACKER, "Test"))
        self.assertEqual(result["error"], "")
        self.assertEqual({r.id for r in self.attacker.added_roles}, {r.id for r in normal})
        self.assertNotIn(str(ATTACKER), antinuke.load_quarantine())

    def test_everyone_is_never_removed(self):
        cog, guild = self.setup_guild()
        self.grant(cog, guild, 1, n=10)
        self.assertFalse(any(r.is_default() for r in self.attacker.removed_roles))

    def test_alert_mode_changes_nothing(self):
        cog, guild = self.setup_guild(mode="alert")
        self.grant(cog, guild, 1, n=12)
        self.assertEqual(self.attacker.removed_roles, [])
        self.assertEqual(antinuke.load_quarantine(), {})
        self.assertTrue(any("Alarmmodus" in c.args[2] for c in cog._alert.await_args_list))

    def test_disabled_protection_does_nothing(self):
        cog, guild = self.setup_guild()
        cog._config = lambda: {"enabled": False, "mode": "protect"}
        self.grant(cog, guild, 1, n=12)
        self.assertEqual(self.attacker.removed_roles, [])

    def test_duplicate_events_for_one_audit_entry_count_once(self):
        cog, guild = self.setup_guild()
        new = roles(5, 1000)
        guild.entries.append(grant_entry(1, ATTACKER, added=new))
        before = SimpleNamespace(roles=[], guild=guild, id=RECIPIENT)
        after = SimpleNamespace(roles=new, guild=guild, id=RECIPIENT)
        for _ in range(4):  # gateway replays the same member update
            asyncio.run(cog.on_member_update(before, after))
        self.assertEqual(self.attacker.removed_roles, [])
        self.assertEqual(sum(n for _t, n in cog._role_grants[(guild.id, ATTACKER)]), 5)

    def test_repeated_threshold_hits_quarantine_only_once(self):
        cog, guild = self.setup_guild()
        self.grant(cog, guild, 1, n=10)
        self.attacker.roles = [r for r in roles(3, 600)]  # regained roles within the cooldown
        self.grant(cog, guild, 2, n=10)
        self.assertEqual(len(self.events("Anti-Nuke: Massen-Rollenvergabe")), 1)
        self.assertEqual(len(self.attacker.removed_roles), 3)

    def test_partial_failure_is_reported_as_partial(self):
        cog, guild = self.setup_guild()

        async def remove(*rs, reason=None):
            if len(rs) > 1 or rs[0].id == 201:
                raise discord.Forbidden(SimpleNamespace(status=403, reason="x"), "no")
            self.attacker.roles = [r for r in self.attacker.roles if r not in rs]
            self.attacker.removed_roles.extend(rs)
        self.attacker.remove_roles = remove
        self.grant(cog, guild, 1, n=10)
        event = self.events("Anti-Nuke: Massen-Rollenvergabe")[0]["payload_json"]
        self.assertIn('"status": "partial"', event)
        stored = antinuke.load_quarantine()[str(ATTACKER)]
        self.assertEqual(len(stored["role_ids"]), 2)
        self.assertEqual([f["id"] for f in stored["failed_roles"]], [201])

    def test_missing_manage_roles_permission_is_a_failure_not_success(self):
        cog, guild = self.setup_guild()
        guild.me.guild_permissions.manage_roles = False
        self.grant(cog, guild, 1, n=10)
        self.assertEqual(self.attacker.removed_roles, [])
        self.assertIn('"status": "failed"', self.events("Anti-Nuke: Massen-Rollenvergabe")[0]["payload_json"])

    def test_state_is_bounded(self):
        cog, guild = self.setup_guild()
        for actor in range(10_000, 10_000 + antinuke.MAX_TRACKED_ACTORS + 50):
            cog._role_grant_total((guild.id, actor), 1.0, 1)
        self.assertLessEqual(len(cog._role_grants), antinuke.MAX_TRACKED_ACTORS)

    def test_dangerous_role_path_of_the_normal_rules_still_works(self):
        rule = base.rule("rechte", ["member_role_grant"], 1, 10, ["log", "alert"])
        cog, guild = self.setup_guild(extra_rules=(rule,))
        admin_role = SimpleNamespace(id=2, permissions=discord.Permissions(administrator=True))
        guild.entries.append(grant_entry(1, ATTACKER, added=[admin_role]))
        before = SimpleNamespace(roles=[], guild=guild, id=RECIPIENT)
        asyncio.run(cog.on_member_update(before, SimpleNamespace(roles=[admin_role], guild=guild, id=RECIPIENT)))
        self.assertTrue(any("Anti-Nuke-Schwelle" in c.args[2] for c in cog._alert.await_args_list))
        self.assertEqual(self.attacker.removed_roles, [])


if __name__ == "__main__":
    unittest.main()
