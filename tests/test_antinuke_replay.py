"""Phase E (complete mission): replay of synthetic Anti-Nuke event sequences through the real cog.

Each scenario is a list of (seconds, actor, event) that is replayed in order – or truly concurrently where Discord
delivers events at the same time. The fake audit log yields control like the real HTTP call does. No Discord
connection, temp database; sanctions are AsyncMocks that are only counted.
"""
import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord

import pulse_db
from cogs import antinuke
from cogs.antinuke import PulseAntiNuke
from tests import test_antinuke_control_center as base
from tests.fakes import FakeMember

ATTACKER, ADMIN_USER, HELPER = 30, 31, 33
TRUSTED_ROLE = 77
KINDS = {"channel_delete": discord.AuditLogAction.channel_delete, "role_delete": discord.AuditLogAction.role_delete,
         "role_update": discord.AuditLogAction.role_update, "member_role_grant": discord.AuditLogAction.member_role_update,
         "ban": discord.AuditLogAction.ban}


class YieldingGuild(base.FakeGuild):
    """Audit-log reads suspend once per call, as the real HTTP request does (so concurrent handlers interleave)."""

    async def audit_logs(self, **kwargs):
        self.audit_calls += 1
        await asyncio.sleep(0)
        for item in list(self.entries):
            if kwargs.get("action") in (None, item.action):
                yield item


class ReplayTests(unittest.TestCase):
    setUp = base.RuleEngineTests.setUp
    tearDown = base.RuleEngineTests.tearDown
    cog = base.RuleEngineTests.cog
    config = base.RuleEngineTests.config

    def replay(self, cog, guild, events, *, concurrent=False):
        """events: [(seconds_from_start, actor_id, event_key)] – each one gets its own audit-log entry and target."""
        async def one(n, actor, key):
            guild.entries.append(base.entry(1000 + n, actor, KINDS[key], target=5000 + n))
            await cog._handle_event(guild, key, 5000 + n)

        async def run():
            start = self.clock.now
            if concurrent:
                await asyncio.gather(*(one(n, actor, key) for n, (_t, actor, key) in enumerate(events)))
                return
            for n, (t, actor, key) in enumerate(events):
                self.clock.now = start + t
                await one(n, actor, key)
        asyncio.run(run())

    def detected(self):
        return [json.loads(e["payload_json"]) for e in pulse_db.events(event_type="Anti-Nuke-Aktion erkannt")]

    def test_concurrent_mass_deletion_is_counted_completely_and_sanctioned_once(self):
        cog = self.cog(self.config(base.rule("kanal", ["channel_delete"], 5, 10, ["log", "alert", "ban"])))
        guild = YieldingGuild()
        self.replay(cog, guild, [(0, ATTACKER, "channel_delete")] * 12, concurrent=True)
        self.assertEqual(len(self.detected()), 12, "kein gleichzeitiges Ereignis darf verloren gehen")
        self.assertEqual(len({d["audit_entry_id"] for d in self.detected()}), 12)
        guild.ban.assert_awaited_once()

    def test_legitimate_admin_mass_action_next_to_an_attack(self):
        lead_role = SimpleNamespace(id=TRUSTED_ROLE, name="Leitung", position=4, permissions=SimpleNamespace())
        admin = FakeMember(ADMIN_USER, "Leitung", roles=[lead_role])
        cog = self.cog(self.config(base.rule("kanal", ["channel_delete"], 3, 10, ["log", "ban"]), trusted_role_ids=[TRUSTED_ROLE]))
        guild = YieldingGuild(members=[admin])
        sequence = [(i * 0.5, ADMIN_USER, "channel_delete") for i in range(10)]  # clean-up of 10 channels
        sequence[4:4] = [(2.0, ATTACKER, "channel_delete"), (2.1, ATTACKER, "channel_delete"), (2.2, ATTACKER, "channel_delete")]
        self.replay(cog, guild, sorted(sequence, key=lambda x: x[0]))
        guild.ban.assert_awaited_once()
        self.assertEqual(guild.ban.await_args.args[0].id, ATTACKER)

    def test_slow_untrusted_mass_action_below_the_rate_never_triggers(self):
        cog = self.cog(self.config(base.rule("kanal", ["channel_delete"], 3, 10, ["log", "ban"])))
        guild = YieldingGuild()
        self.replay(cog, guild, [(i * 6, HELPER, "channel_delete") for i in range(10)])  # one every 6 s
        guild.ban.assert_not_awaited()
        self.assertEqual(len(self.detected()), 10)

    def test_repeated_role_changes_quarantine_once(self):
        danger = SimpleNamespace(id=61, name="Mod", position=3, managed=False, permissions=SimpleNamespace(administrator=True),
                                 is_default=lambda: False)
        member = FakeMember(ATTACKER, "X", roles=[danger])
        member.top_role = base.Pos(3)
        cog = self.cog(self.config(base.rule("rollen", ["role_update", "role_delete"], 3, 10, ["log", "quarantine"])))
        guild = YieldingGuild(members=[member])
        self.replay(cog, guild, [(0, ATTACKER, "role_update"), (1, ATTACKER, "role_delete"), (2, ATTACKER, "role_update"),
                                 (3, ATTACKER, "role_update"), (4, ATTACKER, "role_delete")])
        self.assertEqual(member.removed_roles, [danger])

    def test_dangerous_permission_grant_through_the_real_listener(self):
        cog = self.cog(self.config(base.rule("rechte", ["member_role_grant"], 1, 10, ["log", "alert"])))
        guild = YieldingGuild()
        safe = SimpleNamespace(id=1, permissions=discord.Permissions(send_messages=True))
        admin_role = SimpleNamespace(id=2, permissions=discord.Permissions(administrator=True))
        before = SimpleNamespace(roles=[], guild=guild, id=900)

        async def run():
            guild.entries.append(base.entry(1, ATTACKER, KINDS["member_role_grant"], target=900))
            await cog.on_member_update(before, SimpleNamespace(roles=[safe], guild=guild, id=900))  # harmless role
            await cog.on_member_update(before, SimpleNamespace(roles=[admin_role], guild=guild, id=900))
        asyncio.run(run())
        self.assertEqual(guild.audit_calls, 1, "harmlose Rollen lösen keine Audit-Log-Abfrage aus")
        self.assertTrue(any("Anti-Nuke-Schwelle" in c.args[2] for c in cog._alert.await_args_list))

    def test_rate_limited_ban_is_not_retried_in_a_loop(self):
        cog = self.cog(self.config(base.rule("kanal", ["channel_delete"], 2, 10, ["log", "ban"], cooldown_seconds=600)))
        guild = YieldingGuild()
        guild.ban.side_effect = discord.HTTPException(SimpleNamespace(status=429, reason="Too Many Requests"), "slow down")
        self.replay(cog, guild, [(i * 0.2, ATTACKER, "channel_delete") for i in range(30)])
        self.assertEqual(guild.ban.await_count, 1)
        payload = json.loads(pulse_db.events(event_type="Anti-Nuke ausgelöst")[0]["payload_json"])
        self.assertEqual({s["action"]: s["status"] for s in payload["steps"]}["ban"], "failed")
        self.assertIn("429", {s["action"]: s["detail"] for s in payload["steps"]}["ban"])

    def test_repeated_and_duplicate_gateway_events_count_once_each(self):
        cog = self.cog(self.config(base.rule("kanal", ["channel_delete"], 3, 10, ["log", "ban"])))
        guild = YieldingGuild()
        guild.entries.extend([base.entry(1, ATTACKER, target=1), base.entry(2, ATTACKER, target=2)])

        async def run():  # each deletion is delivered three times (reconnect/resume replays)
            await asyncio.gather(*[cog._handle_event(guild, "channel_delete", t) for t in (1, 2) for _ in range(3)])
        asyncio.run(run())
        self.assertEqual(len(self.detected()), 2)
        guild.ban.assert_not_awaited()

    def test_missing_audit_log_permission_or_forbidden_read_never_sanctions(self):
        cog = self.cog(self.config(base.rule("kanal", ["channel_delete"], 1, 10, ["log", "ban"])))
        guild = YieldingGuild()
        guild.me.guild_permissions.view_audit_log = False
        self.replay(cog, guild, [(0, ATTACKER, "channel_delete")] * 3)
        self.assertEqual(guild.audit_calls, 0)
        guild.me.guild_permissions.view_audit_log = True

        async def forbidden(**_kwargs):
            raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "no audit log")
            yield  # pragma: no cover – makes this an async generator
        guild.audit_logs = forbidden
        self.replay(cog, guild, [(0, ATTACKER, "channel_delete")] * 3)
        guild.ban.assert_not_awaited()

    def test_test_mode_replay_changes_nothing_on_discord(self):
        member = FakeMember(ATTACKER, "X")
        member.top_role = base.Pos(1)
        cog = self.cog(self.config(base.rule("alles", ["channel_delete", "role_delete"], 2, 10,
                                             ["log", "lockdown", "quarantine", "kick", "ban"], test_mode=True),
                                   lockdown_channel_ids=[1]))
        guild = YieldingGuild(members=[member])
        guild.channels = {1: SimpleNamespace(id=1, set_permissions=AsyncMock())}
        self.replay(cog, guild, [(0, ATTACKER, "channel_delete"), (1, ATTACKER, "role_delete")] * 3)
        guild.ban.assert_not_awaited()
        guild.kick.assert_not_awaited()
        guild.channels[1].set_permissions.assert_not_awaited()
        self.assertEqual(member.removed_roles, [])
        self.assertTrue(json.loads(pulse_db.events(event_type="Anti-Nuke ausgelöst")[0]["payload_json"])["dry_run"])


class AutomationChainReplayTests(unittest.TestCase):
    """Anti-Nuke → automation engine: recursion guard and bounded state over a long event replay."""

    def test_steps_never_start_new_chains_and_state_stays_bounded(self):
        import pulse_ultimate as pu
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp, \
                patch.object(pulse_db, "DB_PATH", str(Path(tmp) / "pulse.db")), patch.object(pu, "_INIT", False), \
                patch.object(pu, "_EVENT_SEEN", pu.OrderedDict()), patch.object(pu, "_EVENT_COOLDOWNS", pu.OrderedDict()), \
                patch.object(pu, "_EVENT_TRIGGER_CACHE", {"db": None, "triggers": frozenset()}):
            pulse_db.init_db()
            pu.setup()

            async def nested():
                token = pu._DISPATCH_DEPTH.set(1)  # inside a running automation step
                try:
                    return await pu.dispatch_event(None, "antinuke_event", {"actor_id": 1, "audit_entry_id": 5})
                finally:
                    pu._DISPATCH_DEPTH.reset(token)
            self.assertEqual(asyncio.run(nested()), 0)
            for n in range(2500):
                pu._EVENT_SEEN[("a", n)] = True
                while len(pu._EVENT_SEEN) > 2000:
                    pu._EVENT_SEEN.popitem(last=False)
            self.assertLessEqual(len(pu._EVENT_SEEN), 2000)
        self.assertLessEqual(antinuke.MAX_TRACKED_ACTORS, 2000)
        self.assertTrue(PulseAntiNuke)


if __name__ == "__main__":
    unittest.main()
