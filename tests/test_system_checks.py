"""U-12: extra system checks without a real Discord connection."""
import unittest
from types import SimpleNamespace

import pulse_ids
import pulse_system_checks as sc


def role(rid, position, name="R"):
    return SimpleNamespace(id=rid, position=position, name=name)


class FakeGuild:
    def __init__(self, roles=None, channels=(), me=None):
        self.roles = roles or {}
        self.channels = set(channels)
        self.me = me

    def get_role(self, rid):
        return self.roles.get(rid)

    def get_channel(self, cid):
        return object() if cid in self.channels else None


class SystemCheckTests(unittest.TestCase):
    def test_gateway_and_cog_errors(self):
        self.assertTrue(sc.gateway(SimpleNamespace(latency=0.08))[1])
        self.assertIn("80 ms", sc.gateway(SimpleNamespace(latency=0.08))[2])
        self.assertFalse(sc.gateway(SimpleNamespace(latency=float("inf")))[1])
        self.assertFalse(sc.gateway(None)[1])
        self.assertTrue(sc.cog_errors(SimpleNamespace(pulse_failed_cogs=[]))[1])
        bad = sc.cog_errors(SimpleNamespace(pulse_failed_cogs=["tickets: ImportError"]))
        self.assertFalse(bad[1])
        self.assertIn("tickets: ImportError", bad[2])

    def test_env_shows_names_never_values(self):
        secret = "super-geheim-123"
        full = {n: secret for n in sc.REQUIRED_ENV} | {"PUBLIC_BASE_URL": "https://x"}
        name, ok, detail = sc.env_values(full)
        self.assertTrue(ok)
        self.assertNotIn(secret, detail)
        name, ok, detail = sc.env_values({"DISCORD_TOKEN": secret})
        self.assertFalse(ok)
        self.assertIn("DISCORD_CLIENT_SECRET", detail)
        self.assertNotIn("SESSION_SECRET,", detail)  # has its own check with the .session_secret fallback
        self.assertIn("PUBLIC_BASE_URL", detail)
        self.assertNotIn(secret, detail)

    def test_timezone(self):
        self.assertEqual(sc.timezone()[0], "Zeitzonendaten")

    def test_fixed_ids_are_classified(self):
        roles, channels = sc.fixed_ids()
        self.assertIn("WARN_ROLE_IDS", roles)
        self.assertIn("TEAM_UPDATE_CHANNEL_ID", channels)
        self.assertIn("OFFICE_ROLE_IDS", channels)  # voice channels …
        self.assertIn("OFFICE_ROLE_IDS", roles)     # … and their roles
        self.assertNotIn("GUILD_ID", roles | channels)

    def test_missing_ids_are_named(self):
        roles, channels = sc.fixed_ids()
        all_channels = {i for ids in channels.values() for i in ids}
        guild = FakeGuild(channels=all_channels - {pulse_ids.HONEYPOT_CHANNEL_ID})
        result = {name: (ok, detail) for name, ok, detail in sc.discord_ids(guild)}
        self.assertFalse(result["Feste Kanäle"][0])
        self.assertIn("HONEYPOT_CHANNEL_ID", result["Feste Kanäle"][1])
        self.assertFalse(result["Feste Rollen"][0])
        self.assertFalse(sc.discord_ids(None)[0][1])

    def test_role_hierarchy(self):
        perms = SimpleNamespace(manage_roles=True)
        top = role(1, 10, "Pulse")
        warn = pulse_ids.WARN_ROLE_IDS[1]
        guild = FakeGuild(roles={warn: role(warn, 5, "Warn 1"), 77: role(77, 3)},
                          me=SimpleNamespace(top_role=top, guild_permissions=perms))
        self.assertTrue(sc.role_hierarchy(guild, {"team_role_ids": ["77"]})[1])
        guild.roles[77] = role(77, 12, "Leitung")
        name, ok, detail = sc.role_hierarchy(guild, {"team_role_ids": ["77"]})
        self.assertFalse(ok)
        self.assertIn("Leitung", detail)
        guild.me = SimpleNamespace(top_role=top, guild_permissions=SimpleNamespace(manage_roles=False))
        self.assertIn("Rollen verwalten", sc.role_hierarchy(guild, {})[2])
        self.assertFalse(sc.role_hierarchy(FakeGuild(), {})[1])

    def test_run_never_raises_without_discord(self):
        checks = sc.run(None, None, {}, environ={})
        self.assertTrue(all(len(c) == 3 for c in checks))


if __name__ == "__main__":
    unittest.main()
