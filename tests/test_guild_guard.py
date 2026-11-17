"""DISC-8: Pulse acts only in explicitly allowed guilds (home, configured Recovery/backup server, env list)."""
import asyncio
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import pulse_guild_guard as gg
from pulse_command_errors import PulseCommandTree

HOME = 111
BACKUP = 222
STRANGER = 333


class AllowlistTests(unittest.TestCase):
    def setUp(self):
        env = {"DISCORD_GUILD_ID": str(HOME), "PULSE_ALLOWED_GUILD_IDS": ""}
        self.env = patch.dict(os.environ, env)
        self.env.start()
        self.addCleanup(self.env.stop)

    def allowed(self, guild_id, backup=0):
        with patch.object(gg, "_recovery_backup_id", return_value=backup):
            return gg.is_allowed_guild(guild_id)

    def test_home_and_configured_backup_server_are_allowed(self):
        self.assertTrue(self.allowed(HOME))
        self.assertTrue(self.allowed(BACKUP, backup=BACKUP))

    def test_stranger_and_unconfigured_backup_are_refused(self):
        self.assertFalse(self.allowed(STRANGER))
        self.assertFalse(self.allowed(BACKUP, backup=0))

    def test_env_list_extends_the_allowlist(self):
        with patch.dict(os.environ, {"PULSE_ALLOWED_GUILD_IDS": f"{STRANGER}; 444"}):
            self.assertTrue(self.allowed(STRANGER))
            self.assertTrue(self.allowed(444))

    def test_direct_messages_and_unknown_home_do_not_block(self):
        self.assertTrue(self.allowed(None))
        with patch.object(gg, "_home_guild_id", return_value=0):
            self.assertTrue(self.allowed(STRANGER))  # no allowlist can be built -> never brick the bot

    def test_garbage_guild_id_is_refused(self):
        self.assertFalse(self.allowed("abc"))


class CommandTreeTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"DISCORD_GUILD_ID": str(HOME), "PULSE_ALLOWED_GUILD_IDS": ""})
        self.env.start()
        self.addCleanup(self.env.stop)
        p = patch.object(gg, "_recovery_backup_id", return_value=BACKUP)
        p.start()
        self.addCleanup(p.stop)
        self.tree = PulseCommandTree.__new__(PulseCommandTree)

    def check(self, guild_id):
        interaction = NS(guild_id=guild_id, response=NS(send_message=AsyncMock()))
        return asyncio.run(self.tree.interaction_check(interaction)), interaction

    def test_command_in_foreign_guild_is_refused_politely(self):
        ok, interaction = self.check(STRANGER)
        self.assertFalse(ok)
        interaction.response.send_message.assert_awaited_once()

    def test_commands_in_home_and_backup_guild_pass(self):
        self.assertTrue(self.check(HOME)[0])
        self.assertTrue(self.check(BACKUP)[0])

    def test_refusal_for_a_foreign_admin_does_not_reach_a_global_setting(self):
        """The check runs before any command callback, so /pulse-lock of a foreign admin never calls set_setting."""
        from cogs import pulse_suite
        with patch.object(pulse_suite, "set_setting") as write:
            ok, _ = self.check(STRANGER)
        self.assertFalse(ok)
        write.assert_not_called()


class ListenerWiringTests(unittest.IsolatedAsyncioTestCase):
    async def test_automod_ignores_foreign_guilds_before_any_work(self):
        """AutoMod's configuration is the home server's: in a foreign guild it must not even read it."""
        from unittest.mock import MagicMock

        import discord
        from cogs.automod import PulseAutoMod
        cog = PulseAutoMod.__new__(PulseAutoMod)
        cog._config = MagicMock()
        member = MagicMock(spec=discord.Member)
        member.bot = False
        member.id = 5
        member.guild_permissions = NS(manage_messages=False)
        message = NS(guild=NS(id=STRANGER, owner_id=0), author=member, webhook_id=None, delete=AsyncMock())
        with patch.dict(os.environ, {"DISCORD_GUILD_ID": str(HOME), "PULSE_ALLOWED_GUILD_IDS": ""}), patch.object(gg, "_recovery_backup_id", return_value=0):
            await cog.on_message(message)
        cog._config.assert_not_called()
        message.delete.assert_not_awaited()

    async def test_antinuke_ignores_foreign_guilds(self):
        from cogs.antinuke import PulseAntiNuke
        cog = PulseAntiNuke(NS(user=NS(id=1)))
        cog._config = lambda: {"enabled": True, "mode": "protect", "events": [], "rules": []}
        guild = NS(id=STRANGER, me=NS(guild_permissions=NS(view_audit_log=True)), audit_logs=None)
        with patch.dict(os.environ, {"DISCORD_GUILD_ID": str(HOME), "PULSE_ALLOWED_GUILD_IDS": ""}), patch.object(gg, "_recovery_backup_id", return_value=0), \
                patch.object(cog, "_find_entry", new_callable=AsyncMock) as find:
            await cog._handle_event(guild, "channel_delete", 1)
        find.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
