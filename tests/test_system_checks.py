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

    def test_enabled_moderation_reports_missing_bot_permissions(self):
        bot = SimpleNamespace(intents=SimpleNamespace(message_content=False))
        guild = FakeGuild(me=SimpleNamespace(guild_permissions=SimpleNamespace(
            manage_messages=False, view_audit_log=False, ban_members=False,
        )))
        config = {
            "pulse_automod": {"enabled": True},
            "pulse_antinuke": {"enabled": True},
        }
        checks = {name: (ok, detail) for name, ok, detail in sc.moderation_permissions(bot, guild, config)}
        self.assertFalse(checks["AutoMod · Nachrichteninhalt"][0])
        self.assertFalse(checks["AutoMod · Nachrichten löschen"][0])
        self.assertFalse(checks["Anti-Nuke · Audit-Log"][0])
        self.assertFalse(checks["Anti-Nuke · Sperren"][0])

    def test_disabled_moderation_does_not_add_permission_warnings(self):
        self.assertEqual(sc.moderation_permissions(None, None, {}), [])

    def test_sqlite_diagnostics_are_read_only_and_skip_missing_optional_databases(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            for name in ("pulse.db", "pulse_suite.db"):
                with sqlite3.connect(base / name) as connection:
                    connection.execute("CREATE TABLE sample(value TEXT)")
                    connection.execute("INSERT INTO sample(value) VALUES ('kept')")
            before = {name: (base / name).read_bytes() for name in ("pulse.db", "pulse_suite.db")}

            def local_path(_base, name):
                return base / name

            with patch("pulse_backup.sqlite_path", side_effect=local_path):
                name, ok, detail = sc.sqlite_databases(base)

            self.assertEqual(name, "SQLite-Datenbanken")
            self.assertTrue(ok, detail)
            self.assertIn("2 Datenbank(en) geprüft", detail)
            self.assertIn("optionale Datenbank(en) noch nicht angelegt", detail)
            self.assertEqual({key: (base / key).read_bytes() for key in before}, before)
            self.assertFalse((base / "abmeldungen.db").exists())

    def test_sqlite_diagnostics_report_corrupt_database_without_repairing_it(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pulse.db").write_bytes(b"not a sqlite database")
            (base / "pulse_suite.db").write_bytes(b"also corrupt")
            before = {path.name: path.read_bytes() for path in base.iterdir()}

            with patch("pulse_backup.sqlite_path", side_effect=lambda _base, name: base / name):
                name, ok, detail = sc.sqlite_databases(base)

            self.assertFalse(ok)
            self.assertIn("pulse.db", detail)
            self.assertIn("pulse_suite.db", detail)
            self.assertEqual({path.name: path.read_bytes() for path in base.iterdir()}, before)

    def test_sqlite_diagnostics_fail_when_required_primary_database_is_missing(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            with patch("pulse_backup.sqlite_path", side_effect=lambda _base, name: base / name):
                name, ok, detail = sc.sqlite_databases(base)

            self.assertFalse(ok)
            self.assertIn("pulse.db fehlt", detail)
            self.assertFalse((base / "pulse.db").exists())


if __name__ == "__main__":
    unittest.main()
