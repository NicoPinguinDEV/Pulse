"""BOT-07: personal command favorites are scoped, bounded and permission-filtered."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import discord
from discord import app_commands

import pulse_command_favorites as favorites
from cogs.pulse_pro import PulsePro, _pulse_favorites_autocomplete


def build_tree():
    tree = app_commands.CommandTree(discord.Client(intents=discord.Intents.none()))

    async def callback(interaction: discord.Interaction):
        return None

    for name in ("pulse", "pulse-hilfe", "warn", "promote", "setup_buero"):
        tree.add_command(app_commands.Command(name=name, description=f"Beschreibung {name}", callback=callback))
    group = app_commands.Group(name="dienst", description="Dienstverwaltung")
    group.add_command(app_commands.Command(name="status", description="Zeigt Dienststatus", callback=callback))
    tree.add_command(group)
    return tree


class FavoriteStorageTests(unittest.TestCase):
    def test_setting_key_is_user_scoped(self):
        self.assertEqual(favorites.setting_key(123), "pulse_command_favorites:123")
        self.assertNotEqual(favorites.setting_key(123), favorites.setting_key(456))

    def test_normalization_removes_invalid_duplicates_and_limits_entries(self):
        raw = ["/pulse", "pulse", "bad command<script>", "../warn", "team kick"]
        self.assertEqual(
            favorites.normalize_favorites(raw),
            ["pulse", "team kick"],
        )
        self.assertEqual(favorites.normalize_favorites("pulse"), [])

        many = [f"command-{index}" for index in range(favorites.MAX_FAVORITES + 3)]
        self.assertEqual(
            favorites.normalize_favorites(many),
            many[:favorites.MAX_FAVORITES],
        )

    def test_add_requires_current_permission_and_prevents_duplicates(self):
        allowed = {"pulse", "dienst status"}
        self.assertEqual(favorites.add_favorite([], "/pulse", allowed), ["pulse"])
        with self.assertRaisesRegex(ValueError, "nicht freigegeben"):
            favorites.add_favorite([], "warn", allowed)
        with self.assertRaisesRegex(ValueError, "bereits"):
            favorites.add_favorite(["pulse"], "pulse", allowed)

    def test_add_enforces_maximum_and_remove_can_clear_stale_favorites(self):
        stored = [f"command-{index}" for index in range(favorites.MAX_FAVORITES)]
        with self.assertRaisesRegex(ValueError, "höchstens"):
            favorites.add_favorite(stored, "another", {*stored, "another"})
        self.assertEqual(
            favorites.remove_favorite(["pulse", "warn"], "warn"),
            ["pulse"],
        )
        with self.assertRaisesRegex(ValueError, "nicht in deinen Favoriten"):
            favorites.remove_favorite([], "warn")

    def test_help_right_filter_is_reused_for_autocomplete_candidates(self):
        import pulse_command_help as help_module

        tree = build_tree()
        help_module.apply_visibility(tree)
        member = favorites.permitted_command_names(tree, {}, discord.Permissions.none())
        self.assertIn("pulse", member)
        self.assertIn("dienst status", member)
        self.assertNotIn("warn", member)
        self.assertNotIn("promote", member)
        self.assertNotIn("setup_buero", member)

        lead = favorites.permitted_command_names(
            tree,
            {"can_promote": True, "can_manage_team_warnings": True},
            discord.Permissions(manage_guild=True),
        )
        self.assertIn("warn", lead)
        self.assertIn("promote", lead)
        self.assertIn("setup_buero", lead)
        self.assertNotIn("pulse-favoriten", lead)


class FavoriteCommandTests(unittest.TestCase):
    def setUp(self):
        self.tree = build_tree()
        self.user = SimpleNamespace(id=42, guild_permissions=discord.Permissions.none())
        self.interaction = SimpleNamespace(
            guild=object(),
            user=self.user,
            client=SimpleNamespace(tree=self.tree),
            namespace=SimpleNamespace(aktion="hinzufügen"),
            response=SimpleNamespace(send_message=AsyncMock()),
        )
        self.member = SimpleNamespace(id=42)

    def test_autocomplete_offers_only_allowed_commands_and_uses_existing_favorites(self):
        with (
            patch("webserver.load_config", return_value={}),
            patch("webserver.compute_perms", return_value=({"can_view_dashboard": True}, self.member)),
            patch("cogs.pulse_pro.db.get_setting", return_value=["pulse"]),
        ):
            choices = asyncio.run(_pulse_favorites_autocomplete(self.interaction, "dien"))

        self.assertEqual([choice.value for choice in choices], ["dienst status"])

        self.interaction.namespace.aktion = "entfernen"
        with patch("cogs.pulse_pro.db.get_setting", return_value=["pulse", "warn"]):
            choices = asyncio.run(_pulse_favorites_autocomplete(self.interaction, "warn"))
        self.assertEqual([choice.value for choice in choices], ["warn"])

    def test_add_command_saves_only_the_callers_allowed_favorite(self):
        cog = PulsePro.__new__(PulsePro)
        cog.bot = SimpleNamespace(tree=self.tree)
        with (
            patch("webserver.load_config", return_value={}),
            patch("webserver.compute_perms", return_value=({"can_view_dashboard": True}, self.member)),
            patch("cogs.pulse_pro.db.get_setting", return_value=[]),
            patch("cogs.pulse_pro.db.set_setting") as save,
        ):
            asyncio.run(
                PulsePro.pulse_favorites.callback(
                    cog,
                    self.interaction,
                    app_commands.Choice(name="Hinzufügen", value="hinzufügen"),
                    "pulse",
                )
            )

        save.assert_called_once_with("pulse_command_favorites:42", ["pulse"])
        self.interaction.response.send_message.assert_awaited_once()
        self.assertTrue(self.interaction.response.send_message.await_args.kwargs["ephemeral"])

    def test_user_without_dashboard_right_cannot_read_or_change_favorites(self):
        cog = PulsePro.__new__(PulsePro)
        cog.bot = SimpleNamespace(tree=self.tree)
        with (
            patch("webserver.load_config", return_value={}),
            patch("webserver.compute_perms", return_value=({}, None)),
            patch("cogs.pulse_pro.db.get_setting") as read,
            patch("cogs.pulse_pro.db.set_setting") as save,
        ):
            asyncio.run(
                PulsePro.pulse_favorites.callback(
                    cog,
                    self.interaction,
                    app_commands.Choice(name="Hinzufügen", value="hinzufügen"),
                    "pulse",
                )
            )

        read.assert_not_called()
        save.assert_not_called()
        self.interaction.response.send_message.assert_awaited_once()
        self.assertTrue(self.interaction.response.send_message.await_args.kwargs["ephemeral"])


if __name__ == "__main__":
    unittest.main()
